from __future__ import annotations

import sys

import requests

from .capture_hong_kong_realtime_history import (
    fetch_hong_kong_realtime_with_retries,
)
from .hong_kong_realtime_history import save_afternoon_observation


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "weather-alpha-monitor/0.1"})

    observation = fetch_hong_kong_realtime_with_retries(session)
    if observation is None:
        print(
            "连续 3 次无法获取完整的香港天文台实时数据，保留原历史文件",
            file=sys.stderr,
        )
        sys.exit(1)

    record = save_afternoon_observation(observation)
    print("✓ 已保存香港天文台下午实时观测")
    print(f"  captured_at: {record['captured_at']}")
    print(f"  香港天文台当前温度: {record['hko_current_temp']}℃")
    print(f"  香港天文台今日已录得最高温: {record['today_max_temp']}℃")
    print(f"  collection_window: {record['collection_window']}")


if __name__ == "__main__":
    main()
