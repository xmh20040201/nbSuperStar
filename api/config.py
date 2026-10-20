# -*- coding: utf-8 -*-
class GlobalConst:
    AESKey = "u2oh6Vu^HWe4_AES"
    COOKIES_PATH = "cookies.txt"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"'
    }
    VIDEO_HEADERS = {
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842",
    }
    AUDIO_HEADERS = {
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842",
    }

    THRESHOLD = 0.5

    # 视频进度上报允许的最大"虚拟倍速":
    # 实测(2026-10)超星按相邻上报的节奏累计有效观看时长, 位置增量约为真实流逝时间的
    # 2x~4x 时仍能被记账并通过任务点; 再快容易被判异常。配置文件里的 speed 会被限制在此值以内。
    MAX_REPORT_SPEED = 4.0
