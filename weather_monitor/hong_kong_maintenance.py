from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ALLOWED_COMMIT_FILES = {
    "docs/weather_data.json",
    "docs/polymarket_candidates.json",
    "docs/markets_draft.json",
    "docs/markets.json",
    "docs/hong_kong_live_probability.json",
    "data/hong_kong_today_forecast_cache.json",
}

PROTECTED_JSON_FILES = sorted(ALLOWED_COMMIT_FILES)
FORBIDDEN_EXACT = {"weather_forecasts.sqlite", ".env"}
FORBIDDEN_PARTS = {".venv", "__pycache__"}
SECRET_NAME_PARTS = ("key", "secret", "token")
HK_TZ = "Asia/Hong_Kong"


class MaintenanceError(Exception):
    pass


class NoActiveMarket(Exception):
    pass


@dataclass
class RunOptions:
    dry_run: bool
    no_push: bool


def main() -> None:
    args = parse_args()
    options = RunOptions(dry_run=args.dry_run, no_push=args.no_push)
    backups: dict[Path, bytes | None] = {}

    try:
        repo_root = step("1/18", "检查 Git 状态", ensure_git_repo)
        os.chdir(repo_root)
        ensure_clean_worktree()

        step("2/18", "同步远程仓库", lambda: run_git(["pull", "--rebase"], timeout=120))
        backups = backup_files([Path(p) for p in PROTECTED_JSON_FILES])
        step("3/18", "更新香港天气数据", run_weather_monitor_flow)
        candidate_proc = step("4/18", "发现 Polymarket 候选市场", lambda: run_python_module("weather_monitor.polymarket_candidates", timeout=180))
        ensure_polymarket_candidates_fetch_succeeded(candidate_proc)
        step("5/18", "生成 markets 草稿", lambda: run_python_module("weather_monitor.markets_draft", timeout=60))
        step("6/18", "检查可用香港 Polymarket 温度市场", check_active_market_files)

        draft_markets = step("7/18", "校验 markets_draft.json", validate_markets_draft)
        active_markets = draft_markets
        if options.dry_run:
            print("dry-run: 跳过 markets_draft.json -> markets.json 原子复制")
        else:
            step("8/18", "更新 markets.json", lambda: atomic_copy(Path("docs/markets_draft.json"), Path("docs/markets.json")))
            active_markets = load_json_array(Path("docs/markets.json"))

        step("9/18", "运行香港实时概率", lambda: run_python_module("weather_monitor.capture_hong_kong_live_probability", timeout=180))
        probabilities = step("10/18", "校验 hong_kong_live_probability.json", validate_live_probability)
        step("11/18", "校验 markets 与 probabilities 匹配", lambda: validate_market_probability_match(active_markets, probabilities))
        step("12/18", "清理非香港数据", clean_non_hong_kong_data)
        step("13/18", "检查禁止提交文件", ensure_no_forbidden_files)
        changed_allowed = step("14/18", "计算允许提交的数据文件", get_changed_allowed_files)

        if not changed_allowed:
            if options.dry_run:
                restore_files(backups)
                backups = {}
                print("dry-run: 已恢复运行前的数据文件")
            print("没有需要提交的变化")
            return

        print("本次将提交的文件列表:")
        for path in changed_allowed:
            print(f"- {path}")

        if options.dry_run:
            restore_files(backups)
            backups = {}
            print("dry-run: 已恢复运行前的数据文件")
            print("dry-run: 跳过 git add / commit / push")
            return

        step("15/18", "添加允许的数据文件", lambda: git_add_allowed(changed_allowed))
        step("16/18", "提交数据更新", commit_changes)

        if options.no_push:
            print("--no-push: 已提交，跳过推送")
            return

        step("17/18", "推送前同步远程仓库", lambda: run_git(["pull", "--rebase"], timeout=120))
        step("18/18", "推送到远程仓库", push_with_retries)
        print("✓ 推送成功")
    except NoActiveMarket:
        weather_commit_files = {
            "docs/weather_data.json",
            "data/hong_kong_today_forecast_cache.json",
        }

        if options.dry_run:
            restore_files(backups)
            backups = {}
            warn_restore_runtime_sqlite()
            print("当前没有可用的香港 Polymarket 温度市场。")
            print("可能原因：")
            print("- 今日市场已经结束")
            print("- 明日市场尚未上线")
            print()
            print("dry-run: 已恢复运行前的全部数据文件。")
            print("本次无需提交或推送。")
            print("✓ 维护任务正常结束")
            return

        restore_files({
            path: content
            for path, content in backups.items()
            if str(path) not in weather_commit_files
        })
        backups = {}
        warn_restore_runtime_sqlite()

        print("当前没有可用的香港 Polymarket 温度市场。")
        print("可能原因：")
        print("- 今日市场已经结束")
        print("- 明日市场尚未上线")
        print()
        print("已恢复原有市场和概率文件。")
        print("本次抓取的天气数据将继续保留并提交。")

        changed_weather = [
            changed_path
            for changed_path in get_changed_allowed_files()
            if str(changed_path) in weather_commit_files
        ]

        if not changed_weather:
            print("天气数据没有变化，本次无需提交或推送。")
            print("✓ 维护任务正常结束")
            return

        print("本次将提交的天气文件列表:")
        for changed_path in changed_weather:
            print(f"- {changed_path}")

        git_add_allowed(changed_weather)
        commit_changes()

        if options.no_push:
            print("--no-push: 已提交天气更新，跳过推送")
            print("✓ 维护任务正常结束")
            return

        run_git(["pull", "--rebase"], timeout=120)
        push_with_retries()
        print("✓ 天气数据推送成功")
        print("✓ 维护任务正常结束")
        return
    except Exception as exc:
        restore_files(backups)
        warn_restore_runtime_sqlite()
        print(f"失败：{exc}")
        sys.exit(1)
    finally:
        warn_restore_runtime_sqlite()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain Hong Kong weather and Polymarket data.")
    parser.add_argument("--dry-run", action="store_true", help="Run fetch and validation, but do not copy markets, commit, or push.")
    parser.add_argument("--no-push", action="store_true", help="Commit data changes, but do not push.")
    return parser.parse_args()


def step(label: str, title: str, func):
    print(f"[{label}] {title}")
    try:
        result = func()
    except NoActiveMarket:
        raise
    except Exception as exc:
        raise MaintenanceError(f"{title}失败：{exc}") from exc
    return result


def run_command(cmd: list[str], timeout: int, print_output: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if print_output and proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        detail = stderr or stdout or f"exit code {proc.returncode}"
        raise MaintenanceError(f"{' '.join(cmd)} -> {detail}")
    return proc


def run_git(args: list[str], timeout: int = 60, print_output: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command(["git", *args], timeout=timeout, print_output=print_output)


def run_python_module(module: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return run_command([sys.executable, "-m", module], timeout=timeout)


def ensure_polymarket_candidates_fetch_succeeded(proc: subprocess.CompletedProcess[str]) -> None:
    output = "\n".join([proc.stdout or "", proc.stderr or ""])
    if "Polymarket API 请求失败" in output:
        raise MaintenanceError("Polymarket API 请求失败，不能按空市场跳过")


def run_weather_monitor_flow() -> subprocess.CompletedProcess[str]:
    try:
        return run_python_module("weather_monitor", timeout=180)
    finally:
        warn_restore_runtime_sqlite()


def restore_runtime_sqlite() -> None:
    path = Path("weather_forecasts.sqlite")
    if path.exists():
        run_git(["restore", "--", str(path)], timeout=30, print_output=False)


def warn_restore_runtime_sqlite() -> None:
    try:
        restore_runtime_sqlite()
    except Exception as exc:
        print(f"警告：恢复 weather_forecasts.sqlite 失败：{exc}")


def ensure_git_repo() -> Path:
    proc = run_git(["rev-parse", "--show-toplevel"], timeout=20, print_output=False)
    root = Path(proc.stdout.strip())
    if not root.exists():
        raise MaintenanceError("无法定位 Git 仓库根目录")
    return root


def ensure_clean_worktree() -> None:
    changed = git_status_lines()
    if changed:
        print("运行前存在未提交改动，拒绝继续:")
        for line in changed:
            print(line)
        raise MaintenanceError("工作区不干净")
    print("✓ 工作区干净")


def git_status_lines() -> list[str]:
    proc = run_git(["status", "--short"], timeout=20, print_output=False)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def backup_files(paths: list[Path]) -> dict[Path, bytes | None]:
    backups: dict[Path, bytes | None] = {}
    for path in paths:
        backups[path] = path.read_bytes() if path.exists() else None
    return backups


def restore_files(backups: dict[Path, bytes | None]) -> None:
    if not backups:
        return
    for path, data in backups.items():
        if data is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def load_json_array(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MaintenanceError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, list):
        raise MaintenanceError(f"{path} 必须是 JSON 数组")
    return [item for item in payload if isinstance(item, dict)]


def load_raw_json_array(path: Path) -> list[Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MaintenanceError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, list):
        raise MaintenanceError(f"{path} 必须是 JSON 数组")
    return payload


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MaintenanceError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise MaintenanceError(f"{path} 必须是 JSON 对象")
    return payload


def hong_kong_today() -> str:
    return datetime.now(ZoneInfo(HK_TZ)).date().isoformat()


def check_active_market_files() -> None:
    candidates = load_raw_json_array(Path("docs/polymarket_candidates.json"))
    draft = load_raw_json_array(Path("docs/markets_draft.json"))
    if not candidates and not draft:
        raise NoActiveMarket
    print(f"✓ candidates: {len(candidates)} 条，markets_draft: {len(draft)} 条")


def validate_markets_draft() -> list[dict[str, Any]]:
    path = Path("docs/markets_draft.json")
    rows = load_json_array(path)
    today = hong_kong_today()
    if not rows:
        raise MaintenanceError("markets_draft 数量必须大于 0")

    combos = set()
    exact_thresholds = []
    has_below = False
    has_above = False

    for index, row in enumerate(rows, start=1):
        if row.get("city") != "香港":
            raise MaintenanceError(f"第 {index} 条 city 不是香港")
        if row.get("forecast_date") != today:
            raise MaintenanceError(f"第 {index} 条 forecast_date={row.get('forecast_date')}，不是香港今天 {today}")
        threshold = require_number(row.get("threshold"), f"第 {index} 条 threshold")
        condition = row.get("condition")
        combo = (threshold, condition)
        if combo in combos:
            raise MaintenanceError(f"重复 threshold + condition：{combo}")
        combos.add(combo)

        if condition == "<=":
            has_below = True
        elif condition == ">=":
            has_above = True
        elif condition == "=":
            exact_thresholds.append(threshold)
        else:
            raise MaintenanceError(f"第 {index} 条 condition 非法：{condition}")

        yes_price = require_number(row.get("yes_price"), f"第 {index} 条 yes_price")
        if not 0 <= yes_price <= 1:
            raise MaintenanceError(f"第 {index} 条 yes_price 不在 0 到 1 之间")
        if not row.get("url"):
            raise MaintenanceError(f"第 {index} 条 url 为空")
        if not row.get("market_question"):
            raise MaintenanceError(f"第 {index} 条 market_question 为空")

    if not has_below:
        raise MaintenanceError("至少需要一个 <= 档位")
    if not has_above:
        raise MaintenanceError("至少需要一个 >= 档位")
    ensure_consecutive_exact_thresholds(exact_thresholds)
    print(f"✓ markets 校验通过：{len(rows)} 条")
    return rows


def ensure_consecutive_exact_thresholds(thresholds: list[float]) -> None:
    if not thresholds:
        raise MaintenanceError("缺少中间精确温度档位")
    ordered = sorted(thresholds)
    for prev, curr in zip(ordered, ordered[1:]):
        if abs(curr - prev - 1) > 1e-9:
            raise MaintenanceError(f"中间精确温度档位不连续：{ordered}")


def validate_live_probability() -> dict[str, Any]:
    path = Path("docs/hong_kong_live_probability.json")
    payload = load_json_object(path)
    today = hong_kong_today()
    if payload.get("city") != "香港":
        raise MaintenanceError("live probability city 必须等于香港")
    if payload.get("local_date") != today:
        raise MaintenanceError(f"live probability local_date={payload.get('local_date')}，不是香港今天 {today}")
    probability_sum = require_number(payload.get("probability_sum"), "probability_sum")
    if abs(probability_sum - 1) > 1e-6:
        raise MaintenanceError(f"probability_sum 误差超过 1e-6：{probability_sum}")
    probabilities = payload.get("probabilities")
    if not isinstance(probabilities, list) or not probabilities:
        raise MaintenanceError("probabilities 不能为空")
    combos = set()
    for index, item in enumerate(probabilities, start=1):
        if not isinstance(item, dict):
            raise MaintenanceError(f"probabilities 第 {index} 条不是对象")
        combo = (require_number(item.get("threshold"), f"probabilities 第 {index} 条 threshold"), item.get("condition"))
        if combo in combos:
            raise MaintenanceError(f"probabilities threshold + condition 重复：{combo}")
        combos.add(combo)
    sources = payload.get("forecast_sources")
    if not isinstance(sources, list) or len(sources) < 2:
        raise MaintenanceError("forecast_sources 至少需要 2 个")
    if payload.get("current_temp") is None:
        raise MaintenanceError("current_temp 必须存在")
    if payload.get("today_max_temp") is None:
        raise MaintenanceError("today_max_temp 必须存在")
    print(f"✓ 概率总和：{probability_sum:.6f}")
    return payload


def validate_market_probability_match(markets: list[dict[str, Any]], prob_payload: dict[str, Any]) -> None:
    probability_combos = {
        (require_number(item.get("threshold"), "probability threshold"), item.get("condition"))
        for item in prob_payload.get("probabilities", [])
        if isinstance(item, dict)
    }
    matched = 0
    for row in markets:
        combo = (require_number(row.get("threshold"), "market threshold"), row.get("condition"))
        if combo not in probability_combos:
            raise MaintenanceError(f"market 无匹配概率档位：{combo}")
        matched += 1
    print(f"✓ matched: {matched} / {len(markets)}")


def clean_non_hong_kong_data() -> None:
    for path_text in [
        "docs/weather_data.json",
        "docs/polymarket_candidates.json",
        "docs/markets_draft.json",
        "docs/markets.json",
    ]:
        path = Path(path_text)
        rows = load_json_array(path)
        filtered = [row for row in rows if row.get("city") == "香港"]
        atomic_write_json(path, filtered)
        print(f"✓ {path_text}: {len(rows)} -> {len(filtered)}")


def ensure_no_forbidden_files() -> None:
    bad = []
    for line in git_status_lines():
        path = status_path(line)
        lower = path.lower()
        if path in FORBIDDEN_EXACT:
            bad.append(line)
        elif any(part in path.split("/") for part in FORBIDDEN_PARTS):
            bad.append(line)
        elif any(part in lower for part in SECRET_NAME_PARTS):
            bad.append(line)
    if bad:
        print("发现禁止提交文件:")
        for line in bad:
            print(line)
        raise MaintenanceError("禁止提交文件检查失败")
    print("✓ 禁止提交文件检查通过")


def status_path(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return path


def get_changed_allowed_files() -> list[str]:
    status = git_status_lines()
    changed = []
    disallowed = []
    for line in status:
        path = status_path(line)
        if path in ALLOWED_COMMIT_FILES:
            changed.append(path)
        else:
            disallowed.append(line)
    if disallowed:
        print("存在非日常数据文件改动，不允许机器人提交:")
        for line in disallowed:
            print(line)
        raise MaintenanceError("提交范围超出允许列表")
    return sorted(set(changed))


def git_add_allowed(paths: list[str]) -> None:
    run_git(["add", *paths], timeout=30)


def commit_changes() -> None:
    run_git(["commit", "-m", "Update Hong Kong weather and market data"], timeout=60)


def push_with_retries() -> None:
    delays = [5, 10, 15]
    for attempt, delay in enumerate(delays, start=1):
        try:
            run_git(["pull", "--rebase"], timeout=120)
            run_git(["push"], timeout=120)
            print(f"✓ 推送成功，attempt {attempt}")
            return
        except Exception as exc:
            if attempt == len(delays):
                raise
            print(f"推送失败 attempt {attempt}: {exc}")
            print(f"等待 {delay}s 后重试")
            time.sleep(delay)


def atomic_copy(src: Path, dest: Path) -> None:
    data = src.read_bytes()
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def require_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MaintenanceError(f"{label} 必须是数字") from exc
    return number


if __name__ == "__main__":
    main()
