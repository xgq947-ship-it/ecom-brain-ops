from ops_cli.platforms.jst.shared import ensure_scene_file_ready


def test_ensure_scene_file_ready_reuses_valid_scene(tmp_path) -> None:
    scene_path = tmp_path / "scene.json"
    scene_path.write_text("{}", encoding="utf-8")
    calls = {"refresh": 0}

    check = ensure_scene_file_ready(
        scene_path=scene_path,
        read_scene=lambda path: {"scene": path.name},
        validate_scene=lambda scene: {"valid": True, "reason": "ok"},
        refresh_scene=lambda **kwargs: calls.__setitem__("refresh", calls["refresh"] + 1),
        next_command="ops jst product learn",
        missing_label="商品导出 scene",
        invalid_label="商品导出 scene",
    )

    assert calls["refresh"] == 0
    assert check["valid"] is True


def test_ensure_scene_file_ready_refreshes_missing_scene(tmp_path) -> None:
    scene_path = tmp_path / "scene.json"
    refresh_calls: list[bool] = []

    def refresh_scene(*, force: bool) -> None:
        refresh_calls.append(force)
        scene_path.write_text("{}", encoding="utf-8")

    check = ensure_scene_file_ready(
        scene_path=scene_path,
        read_scene=lambda path: {"scene": path.name},
        validate_scene=lambda scene: {"valid": True, "reason": "ok"},
        refresh_scene=refresh_scene,
        next_command="ops jst product learn",
        missing_label="商品导出 scene",
        invalid_label="商品导出 scene",
    )

    assert refresh_calls == [False]
    assert check["valid"] is True


def test_ensure_scene_file_ready_force_refreshes_invalid_existing_scene(tmp_path) -> None:
    scene_path = tmp_path / "scene.json"
    scene_path.write_text("{}", encoding="utf-8")
    refresh_calls: list[bool] = []

    def refresh_scene(*, force: bool) -> None:
        refresh_calls.append(force)

    checks = iter(
        [
            {"valid": False, "reason": "401"},
            {"valid": True, "reason": "ok"},
        ]
    )

    check = ensure_scene_file_ready(
        scene_path=scene_path,
        read_scene=lambda path: {"scene": path.name},
        validate_scene=lambda scene: next(checks),
        refresh_scene=refresh_scene,
        next_command="ops jst product learn",
        missing_label="商品导出 scene",
        invalid_label="商品导出 scene",
    )

    assert refresh_calls == [True]
    assert check["valid"] is True
