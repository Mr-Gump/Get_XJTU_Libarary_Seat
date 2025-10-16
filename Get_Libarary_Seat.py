import sys
import time
import re
import os
import logging
import argparse
from typing import Dict, List, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed


BASE_URL = "http://rg.lib.xjtu.edu.cn:8010"

# 优先使用 lxml 解析器（更快），不可用则回退到内置解析器
try:
    import lxml  # type: ignore  # noqa: F401
    DEFAULT_HTML_PARSER = "lxml"
except Exception:
    DEFAULT_HTML_PARSER = "html.parser"

CODE_REGEX = re.compile(r"\d+")

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

def configure_logging(log_level: str) -> None:
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    logging.basicConfig(level=level_map.get(log_level, logging.INFO), format=LOG_FORMAT)

def parse_args():
    parser = argparse.ArgumentParser(description="图书馆抢座程序")
    parser.add_argument("--cookie", help="Cookie 字符串，可用 LIB_SEAT_COOKIE 环境变量替代")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"], help="日志级别")
    return parser.parse_args()


class LibraryClient:
    def __init__(
        self,
        cookie: str,
        base_url: str = BASE_URL,
        timeout: Tuple[float, float] = (3.05, 10.0),
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self.session = requests.Session()
        retries = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=50)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "Cookie": cookie,
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                ),
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            }
        )

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base_url}{path}"

    def get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Dict:
        resp = self.session.get(self._url(path), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_html(self, path: str, params: Optional[Dict[str, str]] = None) -> BeautifulSoup:
        resp = self.session.get(self._url(path), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, DEFAULT_HTML_PARSER)

    @staticmethod
    def _parse_alert(soup: BeautifulSoup) -> str:
        node = soup.find(class_="alert")
        return node.text.strip() if node else "未知提示"

    def cancel_seat(self, ri_code: str) -> None:
        self.session.get(
            self._url("/my/"), params={"cancel": "1", "ri": str(ri_code)}, timeout=self.timeout
        )

    def choose_seat(self, room: str, seat: str) -> str:
        soup = self.get_html("/seat/", params={"kid": seat, "sp": room})
        return self._parse_alert(soup)

    def update_seat(self, room: str, seat: str) -> str:
        soup = self.get_html("/updateseat/", params={"kid": seat, "sp": room})
        return self._parse_alert(soup)

    def get_my_status(self) -> Tuple[str, str, Optional[str]]:
        soup = self.get_html("/my/")
        items = soup.find_all(class_="bs-calltoaction")
        if not items:
            return "", "", None

        item = items[0]
        info_node = item.find(class_="cta-contents")
        status_node = item.find(class_="cta-button")

        info_text = info_node.text.strip() if info_node else ""
        status_text = status_node.text.strip()[:-5] if status_node else ""

        ri_code: Optional[str] = None
        code_link = status_node.find("a") if status_node else None
        if code_link and code_link.has_attr("onclick"):
            match = CODE_REGEX.search(code_link["onclick"])
            if match:
                ri_code = match.group()

        return info_text, status_text, ri_code

    def list_rooms(self) -> Dict[str, List[int]]:
        data = self.get_json("/qseat", params={"sp": "north2east"})
        rooms = data.get("scount", {}) or {}
        rooms.pop("", None)
        return rooms

    def list_available_seats_for_room(self, room: str) -> List[str]:
        data = self.get_json("/qseat", params={"sp": room})
        seats = data.get("seat", {}) or {}
        seats.pop("", None)
        return [seat_id for seat_id, status in seats.items() if status == 0]


def clean_message(msg: str) -> str:
    return msg[3:].strip() if len(msg) > 3 else msg.strip()


def get_all_available_seats(client: LibraryClient) -> List[Tuple[str, str]]:
    rooms = client.list_rooms()

    candidate_rooms: List[str] = []
    for name, value in rooms.items():
        status_count = 0
        if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], (int, float)):
            status_count = int(value[1])
        elif isinstance(value, (int, float)):
            status_count = int(value)
        if status_count > 0:
            candidate_rooms.append(name)

    available: List[Tuple[str, str]] = []
    if not candidate_rooms:
        return available

    max_workers = min(8, max(1, len(candidate_rooms)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(client.list_available_seats_for_room, room): room for room in candidate_rooms
        }
        for future in as_completed(futures):
            room = futures[future]
            try:
                seats = future.result()
                for seat in seats:
                    available.append((room, seat))
            except Exception as exc:
                logging.warning("获取房间 %s 座位失败: %s", room, exc)

    return available


def prompt_index(max_index: int) -> int:
    while True:
        raw = input(f"请输入选择座位的编号(1-{max_index}，输入0退出):")
        try:
            idx = int(raw)
        except ValueError:
            print("请输入有效数字。")
            continue
        if 0 <= idx <= max_index:
            return idx
        print("超出范围，请重试。")


def show_my_status(client: LibraryClient) -> Optional[str]:
    info_text, status_text, ri_code = client.get_my_status()
    if info_text or status_text or ri_code:
        print("-----------------------------------")
        if info_text:
            print(info_text)
        if status_text:
            print(status_text)
        if ri_code:
            print(ri_code)
        print("-----------------------------------")
    return ri_code


def maintain_lock(client: LibraryClient, room: str, seat: str,
                  loop_seconds: int = 60, step_seconds: float = 1.0,
                  cycle_interval_minutes: int = 29) -> None:
    while True:
        time.sleep(60 * cycle_interval_minutes)
        print("开始刷新锁定...")
        for _ in range(loop_seconds):
            ri_code = show_my_status(client)
            if ri_code:
                time.sleep(step_seconds)
                try:
                    client.cancel_seat(ri_code)
                except Exception as exc:
                    logging.error("取消座位失败: %s", exc)
                time.sleep(step_seconds)
                try:
                    msg = client.choose_seat(room, seat)
                    print(clean_message(msg))
                except Exception as exc:
                    logging.error("重新选座失败: %s", exc)
            else:
                time.sleep(step_seconds)
        print("正在锁定座位...请勿关闭")


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level)
    cookie = args.cookie or os.environ.get("LIB_SEAT_COOKIE") or input("请输入Cookie:")
    client = LibraryClient(cookie=cookie)

    print("正在获取空余座位列表...")
    while True:
        try:
            available_seats = get_all_available_seats(client)
        except Exception as exc:
            logging.warning("获取座位信息失败，将重试: %s", exc)
            time.sleep(3)
            continue

        if not available_seats:
            print("当前无空位! 将在 60 秒后重试。")
            time.sleep(60)
            continue

        for i, (room, seat) in enumerate(available_seats, start=1):
            print(f"{i}. 位置: {room}    座位号: {seat}")

        idx = prompt_index(len(available_seats))
        if idx == 0:
            print("已退出。")
            sys.exit(0)

        room, seat = available_seats[idx - 1]
        msg = client.choose_seat(room, seat)
        print()
        print(clean_message(msg))

        if "抱歉，该座位已被预约" in msg:
            print("-----------------------")
            continue

        show_my_status(client)
        print("正在锁定座位...请勿关闭")
        maintain_lock(client, room, seat)


if __name__ == "__main__":
    main()

    


