"""
猫超店铺推广分析脚本
===================
用途：分析月度商品销售CSV，输出推广开启/调整建议及店铺款式编码清单
用法：python csv_analyzer.py <csv文件路径>

判断阈值说明（可在 THRESHOLDS 中修改）：
  优先推广（🏆）：利润率>=25% 且 销量>=5件 且 退款率<15% 且 均价>=150元 且 当前无推广费
  次级推广（✅）：利润率>=20% 且 销量>=2件 且 退款率<20% 且 均价>=150元 且 当前推广占比<3%
  推广过高预警（⚠️）：当前推广费>0 且 (利润率<15% 或 推广占比>8%)
  建议暂停推广（🛑）：有推广费 且 经营利润<0

集成说明（最小适配，未改动分析逻辑）：
  - 原脚本函数全部保留（clean/to_float/load_csv/parse_products/classify/overall_stats/generate_report）。
  - 新增 analyze_sales_csv(csv_file_path) -> dict，复用上述函数返回结构化结果，供 workflow import。
  - 去掉硬编码路径依赖（仅 main() 作为独立 CLI 时使用）。
"""

import csv
import sys
import os
import io
import contextlib
from datetime import datetime
from collections import defaultdict

# ===================== 阈值配置（按需调整） =====================
THRESHOLDS = {
    # 🏆 优先推广条件
    "priority_op_margin":    0.25,   # 经营利润率 >= 25%
    "priority_min_qty":      10,     # 销售数量 >= 10件
    "priority_max_refund":   0.15,   # 退款率 < 15%
    "priority_min_price":    150,    # 均价 >= 150元

    # ✅ 次级推广条件
    "secondary_op_margin":   0.20,   # 经营利润率 >= 20%
    "secondary_min_qty":     5,      # 销售数量 >= 5件
    "secondary_max_refund":  0.20,   # 退款率 < 20%
    "secondary_min_price":   150,    # 均价 >= 150元
    "secondary_max_ad_pct":  0.03,   # 当前推广占比 < 3%

    # ⚠️ 推广过高预警
    "warning_min_op_margin": 0.15,   # 利润率低于此值 且 有推广 = 预警
    "warning_max_ad_pct":    0.08,   # 推广占比超过此值 = 预警

    # 🛑 建议暂停推广
    "stop_profit_threshold": 0,      # 经营利润 <= 0 且 有推广费
}
# ===============================================================

# 店铺款式编码字段名（用于校验 CSV 结构）
STYLE_CODE_HEADER = "店铺款式编码"


def clean(s):
    """清理字段，去除首尾空格和制表符"""
    return s.strip().replace('\t', '') if s else ''


def to_float(s):
    """字符串转浮点，失败返回0"""
    s = s.strip().replace('%', '').replace(',', '').replace('\t', '')
    if s in ('', '-', '--'):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def load_csv(filepath):
    """加载CSV，返回 (headers, rows)"""
    with open(filepath, encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        headers = next(reader)
        rows = [row for row in reader if len(row) >= 27]
    return headers, rows


def parse_products(rows):
    """
    解析每行数据，返回产品列表
    列索引说明（基于实际CSV结构）：
      0  - 店铺款式编码
      3  - 款式编码(参考) / 商品名称
      6  - 商品销售数量(扣退)
      7  - 商品销售金额(扣退)
      8  - 商品销售成本(扣退)
      19 - 毛利额
      21 - 费用合计
      23 - 推广费
      25 - 经营利润
      43 - 退款数量合计
      81 - 线上推广消耗
    """
    products = []
    for row in rows:
        sku     = clean(row[0])
        name    = clean(row[3])
        qty     = to_float(row[6])
        amt     = to_float(row[7])
        cost    = to_float(row[8])
        gross   = to_float(row[19])
        expense = to_float(row[21])
        ad_fee  = to_float(row[23])      # 推广费（利润表口径）
        profit  = to_float(row[25])      # 经营利润
        refund_qty = to_float(row[43])
        ad_consume = to_float(row[81])   # 线上推广消耗（费用口径）

        total_qty    = qty + refund_qty
        refund_rate  = refund_qty / total_qty if total_qty > 0 else 0
        op_margin    = profit / amt if amt > 0 else 0
        gross_margin = gross / amt if amt > 0 else 0
        ad_pct       = ad_consume / amt if amt > 0 else 0
        avg_price    = amt / qty if qty > 0 else 0

        brand = ''
        if '苏泊尔' in name:
            brand = '苏泊尔'
        elif '奥克斯' in name:
            brand = '奥克斯'

        products.append({
            'sku':          sku,
            'name':         name,
            'brand':        brand,
            'qty':          qty,
            'amt':          amt,
            'cost':         cost,
            'gross':        gross,
            'gross_margin': gross_margin,
            'expense':      expense,
            'ad_fee':       ad_fee,
            'ad_consume':   ad_consume,
            'profit':       profit,
            'op_margin':    op_margin,
            'refund_qty':   refund_qty,
            'refund_rate':  refund_rate,
            'avg_price':    avg_price,
            'ad_pct':       ad_pct,
        })
    return products


def classify(products, th):
    """将产品分类到各推广档位"""
    priority   = []  # 🏆 优先推广
    secondary  = []  # ✅ 次级推广
    warning    = []  # ⚠️ 推广过高
    stop       = []  # 🛑 建议暂停

    for p in products:
        has_ad = p['ad_consume'] > 0

        # 🛑 建议暂停：有推广但亏损
        if has_ad and p['profit'] <= th['stop_profit_threshold']:
            stop.append(p)
            continue

        # ⚠️ 推广过高预警
        if has_ad and (
            p['op_margin'] < th['warning_min_op_margin'] or
            p['ad_pct'] > th['warning_max_ad_pct']
        ):
            warning.append(p)
            continue

        # 🏆 优先推广：无推广 + 高利润 + 足够销量
        if (
            not has_ad and
            p['amt'] > 0 and
            p['op_margin'] >= th['priority_op_margin'] and
            p['qty']       >= th['priority_min_qty'] and
            p['refund_rate']< th['priority_max_refund'] and
            p['avg_price'] >= th['priority_min_price']
        ):
            priority.append(p)
            continue

        # ✅ 次级推广：低/无推广 + 利润合格 + 少量销量验证
        if (
            p['amt'] > 0 and
            p['ad_pct']    <= th['secondary_max_ad_pct'] and
            p['op_margin'] >= th['secondary_op_margin'] and
            p['qty']       >= th['secondary_min_qty'] and
            p['refund_rate']< th['secondary_max_refund'] and
            p['avg_price'] >= th['secondary_min_price']
        ):
            secondary.append(p)

    # 排序：利润率降序，销售额降序
    priority.sort(key=lambda x: (-x['op_margin'], -x['amt']))
    secondary.sort(key=lambda x: (-x['op_margin'], -x['amt']))
    warning.sort(key=lambda x: x['op_margin'])
    stop.sort(key=lambda x: x['profit'])

    return priority, secondary, warning, stop


def overall_stats(products):
    """计算整体汇总指标"""
    total_qty     = sum(p['qty'] for p in products)
    total_amt     = sum(p['amt'] for p in products)
    total_cost    = sum(p['cost'] for p in products)
    total_gross   = sum(p['gross'] for p in products)
    total_expense = sum(p['expense'] for p in products)
    total_profit  = sum(p['profit'] for p in products)
    total_refund  = sum(p['refund_qty'] for p in products)
    total_ad      = sum(p['ad_consume'] for p in products)
    return {
        'sku_count':    len(products),
        'qty':          total_qty,
        'amt':          total_amt,
        'cost':         total_cost,
        'gross':        total_gross,
        'expense':      total_expense,
        'profit':       total_profit,
        'refund_qty':   total_refund,
        'ad_consume':   total_ad,
        'gross_margin': total_gross / total_amt if total_amt else 0,
        'op_margin':    total_profit / total_amt if total_amt else 0,
        'refund_rate':  total_refund / (total_qty + total_refund) if (total_qty + total_refund) else 0,
        'ad_pct':       total_ad / total_amt if total_amt else 0,
    }


def fmt_pct(v):
    return f"{v*100:.1f}%"


def fmt_money(v):
    return f"{v:,.0f}"


def print_product_row(p, index=None):
    prefix = f"  {index}. " if index else "  "
    name_show = (p['name'][:38] + '…') if len(p['name']) > 38 else p['name']
    if not name_show:
        name_show = '（无商品名称）'
    print(f"{prefix}店铺款式编码: {p['sku']}")
    print(f"     商品名称: {name_show}")
    print(f"     销售额={fmt_money(p['amt'])}元  数量={p['qty']:.0f}件  "
          f"利润率={fmt_pct(p['op_margin'])}  毛利率={fmt_pct(p['gross_margin'])}  "
          f"退款率={fmt_pct(p['refund_rate'])}  均价={p['avg_price']:.0f}元  "
          f"当前推广={fmt_money(p['ad_consume'])}元({fmt_pct(p['ad_pct'])})")
    print()


def generate_report(filepath, products, stats, priority, secondary, warning, stop):
    """生成完整分析报告"""
    sep  = "=" * 70
    sep2 = "-" * 70
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(sep)
    print(f"  猫超店铺推广分析报告")
    print(f"  文件: {os.path.basename(filepath)}")
    print(f"  生成时间: {now}")
    print(sep)

    # ── 整体概况
    print("\n【整体概况】")
    print(f"  SKU数量   : {stats['sku_count']} 个")
    print(f"  销售数量  : {stats['qty']:.0f} 件")
    print(f"  退款数量  : {stats['refund_qty']:.0f} 件（退款率 {fmt_pct(stats['refund_rate'])}）")
    print(f"  销售金额  : {fmt_money(stats['amt'])} 元")
    print(f"  毛利额    : {fmt_money(stats['gross'])} 元（毛利率 {fmt_pct(stats['gross_margin'])}）")
    print(f"  经营利润  : {fmt_money(stats['profit'])} 元（利润率 {fmt_pct(stats['op_margin'])}）")
    print(f"  推广消耗  : {fmt_money(stats['ad_consume'])} 元（推广占比 {fmt_pct(stats['ad_pct'])}）")

    # ── 🏆 优先推广
    print(f"\n{sep2}")
    print(f"🏆 优先推广（{len(priority)} 个）")
    print(f"   条件：利润率≥{fmt_pct(THRESHOLDS['priority_op_margin'])}  "
          f"销量≥{THRESHOLDS['priority_min_qty']}件  "
          f"退款率<{fmt_pct(THRESHOLDS['priority_max_refund'])}  "
          f"均价≥{THRESHOLDS['priority_min_price']}元  当前无推广")
    print(f"   推荐计划：全站推广（优先）→ 万相台关键词推广（叠加）")
    print(f"   目标推广费占比：4%~8%\n")
    if priority:
        for i, p in enumerate(priority, 1):
            print_product_row(p, i)
    else:
        print("  暂无符合条件商品\n")

    # ── 🏆 SKU编码清单（方便直接复制）
    if priority:
        print(f"  ▶ 店铺款式编码清单（可直接复制）:")
        for p in priority:
            print(f"    {p['sku']}")
        print()

    # ── ✅ 次级推广
    print(f"{sep2}")
    print(f"✅ 次级推广（{len(secondary)} 个）")
    print(f"   条件：利润率≥{fmt_pct(THRESHOLDS['secondary_op_margin'])}  "
          f"销量≥{THRESHOLDS['secondary_min_qty']}件  "
          f"退款率<{fmt_pct(THRESHOLDS['secondary_max_refund'])}  "
          f"当前推广占比<{fmt_pct(THRESHOLDS['secondary_max_ad_pct'])}")
    print(f"   推荐计划：全站推广小预算测试，日预算100~200元/品\n")
    if secondary:
        for i, p in enumerate(secondary, 1):
            print_product_row(p, i)
    else:
        print("  暂无符合条件商品\n")

    if secondary:
        print(f"  ▶ 店铺款式编码清单（可直接复制）:")
        for p in secondary:
            print(f"    {p['sku']}")
        print()

    # ── ⚠️ 推广过高预警
    print(f"{sep2}")
    print(f"⚠️  推广过高预警（{len(warning)} 个）— 建议降低出价或缩减预算")
    print(f"   触发条件：利润率<{fmt_pct(THRESHOLDS['warning_min_op_margin'])} 或 推广占比>{fmt_pct(THRESHOLDS['warning_max_ad_pct'])}\n")
    if warning:
        for i, p in enumerate(warning, 1):
            reasons = []
            if p['op_margin'] < THRESHOLDS['warning_min_op_margin']:
                reasons.append(f"利润率仅{fmt_pct(p['op_margin'])}")
            if p['ad_pct'] > THRESHOLDS['warning_max_ad_pct']:
                reasons.append(f"推广占比{fmt_pct(p['ad_pct'])}过高")
            reason_str = "，".join(reasons)
            print_product_row(p, i)
            print(f"     ⚠ 预警原因: {reason_str}")
            print()
    else:
        print("  暂无预警商品\n")

    # ── 🛑 建议暂停推广
    print(f"{sep2}")
    print(f"🛑 建议暂停推广（{len(stop)} 个）— 推广中但经营亏损")
    if stop:
        for i, p in enumerate(stop, 1):
            print_product_row(p, i)
            print(f"     ⚠ 经营利润: {fmt_money(p['profit'])}元（亏损），推广消耗: {fmt_money(p['ad_consume'])}元\n")
    else:
        print("  暂无需暂停商品\n")

    print(sep)
    print("  分析完毕")
    print(sep)


def export_skus(priority, secondary, filepath):
    """导出店铺款式编码到txt，方便workflow读取（输出到脚本所在目录）"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    basename   = os.path.splitext(os.path.basename(filepath))[0]
    out_path   = os.path.join(script_dir, f"{basename}_推广SKU清单.txt")

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# 猫超推广SKU清单\n")
        f.write(f"# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# 来源文件: {os.path.basename(filepath)}\n\n")

        f.write("## 🏆 优先推广\n")
        for p in priority:
            name_show = p['name'][:40] if p['name'] else '无名称'
            f.write(f"{p['sku']}\t{name_show}\t利润率{p['op_margin']*100:.1f}%\t销量{p['qty']:.0f}件\n")

        f.write("\n## ✅ 次级推广\n")
        for p in secondary:
            name_show = p['name'][:40] if p['name'] else '无名称'
            f.write(f"{p['sku']}\t{name_show}\t利润率{p['op_margin']*100:.1f}%\t销量{p['qty']:.0f}件\n")

    return out_path


def _dedup_style_codes(*groups):
    """合并多个分类的店铺款式编码，去重且保序。"""
    seen = set()
    ordered = []
    for group in groups:
        for p in group:
            code = p['sku']
            if not code or code in seen:
                continue
            seen.add(code)
            ordered.append(code)
    return ordered


# ─────────────────────────────────────────────────────────────────────────
# 列归一化 shim（适配层，不改动上方原脚本的任何分析逻辑）
#
# 背景：parse_products 用固定列下标（0,3,6,7,8,19,21,23,25,43,81）解析，这些下标是
# 按运营手工导出的某一种列布局标定的。聚水潭「商品利润」导出在不同「显示出比率/费用层级」
# 设置下列数会变（含/不含「-占比」列等），导致同名列出现在不同下标。
#
# 本 shim 只做「按表头名把真实 CSV 的列搬到 parse_products 期望的下标位置」，
# 再交给原 parse_products 解析。原脚本的阈值、分类、报告逻辑一律不动。
# 当 CSV 本就是标定布局（如运营原始文件）时，按名查找命中原位，结果完全一致。

# canonical 下标 -> 该角色可接受的表头名（去空白后精确匹配，按优先级）
_CANONICAL_COLUMN_HEADERS = {
    0:  ["店铺款式编码"],
    3:  ["款式编码(参考)", "商品名称", "线上商品名称"],
    6:  ["商品销售数据-商品销售数量(扣退)", "利润-销售数量(扣退)", "商品销售数量(扣退)", "销售数量(扣退)"],
    7:  ["商品销售数据-商品销售金额(扣退)", "利润-销售金额(扣退)", "商品销售金额(扣退)", "销售金额(扣退)"],
    8:  ["商品销售数据-商品销售成本(扣退)", "利润-销售成本(扣退)", "商品销售成本(扣退)", "销售成本(扣退)"],
    19: ["利润-毛利额", "毛利额"],
    21: ["利润-费用", "费用"],
    23: ["利润-其中：推广费", "其中：推广费", "推广费"],
    25: ["利润-经营利润", "经营利润"],
    43: ["退款合计-退款数量合计", "退款数量合计"],
    81: ["商品费用-线上推广消耗", "线上推广消耗"],
}
# 缺失即无法分析的关键列（销量/金额/利润等）
_REQUIRED_CANONICAL = {0, 6, 7, 8, 19, 21, 23, 25, 43, 81}
_CANONICAL_WIDTH = 82  # parse_products 需要的最小行宽（最大下标 81 + 1）


def _normalize_rows_by_header(headers: list, rows: list) -> list:
    """把真实 CSV 的列按表头名搬到 parse_products 期望的固定下标位置。"""
    norm_headers = [clean(h) for h in headers]
    header_index = {}
    for idx, name in enumerate(norm_headers):
        header_index.setdefault(name, idx)

    resolved: dict[int, int] = {}
    missing: list[str] = []
    for canonical_pos, candidates in _CANONICAL_COLUMN_HEADERS.items():
        src = next((header_index[c] for c in candidates if c in header_index), None)
        if src is None:
            if canonical_pos in _REQUIRED_CANONICAL:
                missing.append(candidates[0])
            continue
        resolved[canonical_pos] = src

    if missing:
        raise ValueError(
            "CSV 缺少分析所需的关键列（按表头名定位失败）："
            + "、".join(missing)
            + "。请确认导出包含完整利润表列（导出弹窗费用层级选「二级利润表项目」）。"
        )

    normalized: list = []
    for row in rows:
        new_row = [""] * _CANONICAL_WIDTH
        for canonical_pos, src in resolved.items():
            if src < len(row):
                new_row[canonical_pos] = row[src]
        normalized.append(new_row)
    return normalized


def analyze_sales_csv(csv_file_path: str) -> dict:
    """workflow 集成入口：分析商品销售 CSV，返回结构化结果。

    复用原脚本 load_csv / parse_products / classify / overall_stats / generate_report，
    不重写分析逻辑。仅在调用 parse_products 前用 _normalize_rows_by_header 按表头名归一化列，
    使不同导出列布局都能复用原脚本固定下标解析。返回店铺款式编码清单（优先+次级，去重保序）。
    """
    if not os.path.exists(csv_file_path):
        raise FileNotFoundError(f"CSV 文件不存在：{csv_file_path}")

    headers, rows = load_csv(csv_file_path)
    cleaned_headers = [clean(h) for h in headers]
    if STYLE_CODE_HEADER not in cleaned_headers:
        raise ValueError(
            f"CSV 缺少必需字段「{STYLE_CODE_HEADER}」，无法输出店铺款式编码。"
            f"实际表头前若干列：{cleaned_headers[:5]}"
        )

    normalized_rows = _normalize_rows_by_header(headers, rows)
    products = parse_products(normalized_rows)
    priority, secondary, warning, stop = classify(products, THRESHOLDS)
    stats = overall_stats(products)

    style_codes = _dedup_style_codes(priority, secondary)

    # 复用原 generate_report 文本（不改其 print 逻辑），仅捕获为字符串。
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        generate_report(csv_file_path, products, stats, priority, secondary, warning, stop)
    report_text = buffer.getvalue()

    def _records(group: list) -> list:
        return [
            {
                "sku": p["sku"],
                "name": p["name"] or "（无商品名称）",
                "op_margin": p["op_margin"],
                "gross_margin": p["gross_margin"],
                "qty": p["qty"],
                "amt": p["amt"],
                "avg_price": p["avg_price"],
                "refund_rate": p["refund_rate"],
                "ad_consume": p["ad_consume"],
                "ad_pct": p["ad_pct"],
            }
            for p in group
        ]

    return {
        "style_codes": style_codes,
        "total_rows": len(products),
        "matched_rows": len(priority) + len(secondary),
        "unique_style_code_count": len(style_codes),
        "categories": {
            "priority": [p["sku"] for p in priority],
            "secondary": [p["sku"] for p in secondary],
            "warning": [p["sku"] for p in warning],
            "stop": [p["sku"] for p in stop],
        },
        # 分档位商品明细（店铺款式编码 / 商品名称 / 利润率 / 销量 …），供 Excel 推广清单输出
        "details": {
            "priority": _records(priority),
            "secondary": _records(secondary),
            "warning": _records(warning),
            "stop": _records(stop),
        },
        "stats": stats,
        "report_text": report_text,
    }


def main():
    if len(sys.argv) < 2:
        # 尝试自动查找当前目录下的CSV
        csv_files = [f for f in os.listdir('.') if f.endswith('.csv')]
        if len(csv_files) == 1:
            filepath = csv_files[0]
            print(f"自动识别CSV文件: {filepath}\n")
        elif len(csv_files) > 1:
            print("发现多个CSV文件，请指定文件路径：")
            for f in csv_files:
                print(f"  {f}")
            print(f"\n用法: python {sys.argv[0]} <csv文件路径>")
            sys.exit(1)
        else:
            print(f"用法: python {sys.argv[0]} <csv文件路径>")
            sys.exit(1)
    else:
        filepath = sys.argv[1]

    if not os.path.exists(filepath):
        print(f"错误：文件不存在 → {filepath}")
        sys.exit(1)

    # 加载 & 解析
    headers, rows = load_csv(filepath)
    products = parse_products(rows)

    if not products:
        print("未读取到有效数据，请检查CSV格式。")
        sys.exit(1)

    # 分类
    priority, secondary, warning, stop = classify(products, THRESHOLDS)

    # 整体统计
    stats = overall_stats(products)

    # 输出报告
    generate_report(filepath, products, stats, priority, secondary, warning, stop)

    # 导出SKU清单txt
    out_path = export_skus(priority, secondary, filepath)
    print(f"\n📄 SKU清单已导出: {out_path}")


if __name__ == '__main__':
    main()
