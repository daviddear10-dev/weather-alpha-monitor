from __future__ import annotations

import sys

import requests

from .hong_kong_realtime import fetch_hong_kong_realtime
from .hong_kong_realtime_history import save_realtime_observation


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "weather-alpha-monitor/0.1"})

    observation = fetch_hong_kong_realtime(session=session)
    if observation is None or observation.current_temp is None:
        print("无法获取香港天文台实时温度", file=sys.stderr)
        sys.exit(1)
    if observation.today_max_temp is None:
        print("无法获取香港天文台今日已录得最高温", file=sys.stderr)
        sys.exit(1)

    record = save_realtime_observation(observation)
    print("✓ 已保存香港天文台实时观测")
    print(f"  captured_at: {record['captured_at']}")
    print(f"  observed_at: {record['observed_at'] or '-'}")
    print(f"  current_temp: {record['current_temp']}℃")
    print(f"  today_max_temp: {record['today_max_temp']}℃")


if __name__ == "__main__":
    main()
