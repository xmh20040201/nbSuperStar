import configparser
import json
import os
import random
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from re import sub
from typing import Optional

import requests
from urllib3 import disable_warnings, exceptions

from api.answer_check import check_answer
from api.logger import logger

# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)

__all__ = ["CacheDAO", "Tiku", "TikuFallback", "TikuYanxi", "TikuGo", "TikuLike", "TikuAdapter"]

class CacheDAO:
    """
    @Author: SocialSisterYi
    @Reference: https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy

    注意：锁必须是【类级/模块级】的。调用方（Tiku.query）每次都会 new 一个实例，
    若用实例级锁，不同线程各持一把锁，跨实例毫无互斥，add_cache 的
    “读-改-写”会互相覆盖，导致缓存大量丢失。
    """
    DEFAULT_CACHE_FILE = "cache.json"

    # 进程内所有 CacheDAO 实例共享同一把锁，保证并发读-改-写的互斥
    _global_lock = threading.RLock()
    # 按缓存文件路径区分锁，避免不同文件互相阻塞
    _locks_guard = threading.Lock()
    _locks_by_path: dict[str, threading.RLock] = {}

    def __init__(self, file: str = DEFAULT_CACHE_FILE):
        self.cache_file = Path(file)
        self._lock = self._get_path_lock(self.cache_file)
        if not self.cache_file.is_file():
            self._write_cache({})

    @classmethod
    def _get_path_lock(cls, path: Path) -> threading.RLock:
        """为每个缓存文件路径返回同一把全局锁"""
        key = str(path.resolve()).lower()
        with cls._locks_guard:
            lock = cls._locks_by_path.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._locks_by_path[key] = lock
            return lock

    def _read_cache(self) -> dict:
        # 新增缓存文件读取的异常处理
        try:
            with self._lock:
                if not self.cache_file.is_file():
                    return {}
                try:
                    with self.cache_file.open("r", encoding="utf8") as fp:
                        return json.load(fp)
                except json.JSONDecodeError as e:
                    logger.error(f"缓存文件 JSON 解析失败: {e}, 尝试恢复...")
                    # 尝试从原始二进制中以 utf-8 忽略错误地恢复有效 JSON 段
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                return json.loads(text[start:end+1])
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # 若无法恢复，备份损坏文件并返回空缓存
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        shutil.copy2(self.cache_file, bak_path)
                        logger.error(f"缓存文件已损坏，已备份为: {bak_path}，将使用空缓存继续运行")
                    except Exception as ex:
                        logger.error(f"备份损坏缓存失败: {ex}")
                    return {}
                except UnicodeDecodeError as e:
                    logger.error(f"缓存文件编码读取失败: {e}, 采用恢复策略...")
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                return json.loads(text[start:end+1])
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        shutil.copy2(self.cache_file, bak_path)
                        logger.error(f"缓存文件编码错误，已备份为: {bak_path}，将使用空缓存继续运行")
                    except Exception as ex:
                        logger.error(f"备份损坏缓存失败: {ex}")
                    return {}
        except Exception as e:
            logger.error(f"读取缓存异常: {e}")
            return {}

    def _write_cache(self, data: dict) -> None:
        # 为缓存写入加锁，防止并发写入损坏文件
        try:
            with self._lock:
                parent = self.cache_file.parent
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
                # 写入临时文件后原子替换，减少并发写入时的损坏风险
                fd, tmp_path = tempfile.mkstemp(prefix=self.cache_file.name, dir=str(parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf8") as fp:
                        json.dump(data, fp, ensure_ascii=False, indent=4)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(tmp_path, str(self.cache_file))
                except Exception as e:
                    # 清理临时文件
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    logger.error(f"Failed to write cache atomically: {e}")
        except IOError as e:
            logger.error(f"Failed to write cache: {e}")

    def get_cache(self, question: str) -> Optional[str]:
        with self._lock:
            data = self._read_cache()
            return data.get(question)

    def add_cache(self, question: str, answer: str) -> None:
        # 锁覆盖整个 读-改-写 过程，防止并发时的丢失更新
        with self._lock:
            data = self._read_cache()
            data[question] = answer
            self._write_cache(data)


# TODO: 重构此部分代码，将此类改为抽象类，加载题库方法改为静态方法，禁止直接初始化此类
class Tiku:
    CONFIG_PATH = os.path.join(os.getcwd(), "config.ini")  # TODO: 从运行参数中获取config路径
    DISABLE = False     # 停用标志
    SUBMIT = False      # 提交标志
    COVER_RATE = 0.8    # 覆盖率
    true_list = []
    false_list = []
    def __init__(self) -> None:
        self._name = None
        self._api = None
        self._conf = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def api(self):
        return self._api

    @api.setter
    def api(self, value):
        self._api = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, value):
        self._token = value

    def init_tiku(self):
        # 仅用于题库初始化, 应该在题库载入后作初始化调用, 随后才可以使用题库
        # 尝试根据配置文件设置提交模式
        if not self._conf:
            self.config_set(self._get_conf())
        if not self.DISABLE:
            # 设置提交模式
            self.SUBMIT = True if self._conf['submit'] == 'true' else False
            self.COVER_RATE = float(self._conf['cover_rate'])
            self.true_list = self._conf['true_list'].split(',')
            self.false_list = self._conf['false_list'].split(',')
            # 调用自定义题库初始化
            self._init_tiku()

    def _init_tiku(self):
        # 仅用于题库初始化, 例如配置token, 交由自定义题库完成
        pass

    def config_set(self,config):
        self._conf = config

    def _get_conf(self):
        """
        从默认配置文件查询配置, 如果未能查到, 停用题库
        """
        try:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_PATH, encoding="utf8")
            return config['tiku']
        except (KeyError, FileNotFoundError):
            logger.info("未找到tiku配置, 已忽略题库功能")
            self.DISABLE = True
            return None
        
    def query(self,q_info:dict) -> Optional[str]:
        if self.DISABLE:
            return None

        # 预处理, 去除【单选题】这样与标题无关的字段
        logger.debug(f"原始标题：{q_info['title']}")
        q_info['title'] = sub(r'^\d+', '', q_info['title'])
        q_info['title'] = sub(r'（\d+\.\d+分）$', '', q_info['title'])
        logger.debug(f"处理后标题：{q_info['title']}")

        # 先过缓存
        cache_dao = CacheDAO()
        answer = cache_dao.get_cache(q_info['title'])
        if answer:
            logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
            return answer.strip()
        else:
            answer = self._query(q_info)
            if answer:
                answer = answer.strip()
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")
                if check_answer(answer, q_info['type'], self):
                    cache_dao.add_cache(q_info['title'], answer)
                    return answer
                else:
                    logger.info(f"从{self.name}获取到的答案类型与题目类型不符，已舍弃")
                    return None

            logger.error(f"从{self.name}获取答案失败：{q_info['title']}")
        return None



    def _query(self, q_info:dict) -> Optional[str]:
        """
        查询接口, 交由自定义题库实现
        """
        pass


    def get_tiku_from_config(self):
        """
        从配置文件加载题库, 这个配置可以是用户提供, 可以是默认配置文件
        """
        if not self._conf:
            # 尝试从默认配置文件加载
            self.config_set(self._get_conf())
        if self.DISABLE:
            return self
        try:
            cls_name = self._conf['provider']
            if not cls_name:
                raise KeyError
        except KeyError:
            self.DISABLE = True
            logger.error("未找到题库配置, 已忽略题库功能")
            return self

        providers = [name.strip() for name in cls_name.split(',') if name.strip()]
        if not providers:
            self.DISABLE = True
            logger.error("题库provider配置为空, 已忽略题库功能")
            return self

        invalid_providers = [name for name in providers if name not in PROVIDER_REGISTRY]
        if invalid_providers:
            self.DISABLE = True
            logger.error(f"题库provider配置无效: {', '.join(invalid_providers)}")
            return self

        if len(providers) == 1:
            provider_cls = PROVIDER_REGISTRY[providers[0]]
            if not isinstance(provider_cls, type) or not issubclass(provider_cls, Tiku):
                self.DISABLE = True
                logger.error(f"题库provider配置无效: {providers[0]}")
                return self
            new_cls = provider_cls()
            new_cls.config_set(self._conf)
            return new_cls

        chain_providers = []
        for provider_name in providers:
            provider_cls = PROVIDER_REGISTRY[provider_name]
            if not isinstance(provider_cls, type) or not issubclass(provider_cls, Tiku):
                self.DISABLE = True
                logger.error(f"题库provider配置无效: {provider_name}")
                return self
            provider = provider_cls()
            provider.config_set(self._conf)
            chain_providers.append(provider)
        fallback = TikuFallback(chain_providers)
        fallback.config_set(self._conf)
        return fallback

    def judgement_select(self, answer: str) -> bool:
        """
        这是一个专用的方法, 要求配置维护两个选项列表, 一份用于正确选项, 一份用于错误选项, 以应对题库对判断题答案响应的各种可能的情况
        它的作用是将获取到的答案answer与可能的选项列对比并返回对应的布尔值
        """
        if self.DISABLE:
            return False
        # 对响应的答案作处理
        answer = answer.strip()
        if answer in self.true_list:
            return True
        elif answer in self.false_list:
            return False
        else:
            # 无法判断, 随机选择
            logger.error(f'无法判断答案 -> {answer} 对应的是正确还是错误, 请自行判断并加入配置文件重启脚本, 本次将会随机选择选项')
            return random.choice([True,False])

    def get_submit_params(self):
        """
        这是一个专用方法, 用于根据当前设置的提交模式, 响应对应的答题提交API中的pyFlag值
        """
        # 留空直接提交, 1保存但不提交
        if self.SUBMIT:
            return ""
        else:
            return "1"

    def check_llm_connection(self) -> bool:
        """
        检查大模型连接是否可用
        默认返回 True（非大模型题库不需要检查）
        """
        return True


class TikuFallback(Tiku):
    # 多题库回退实现，按 provider 中配置顺序依次查询。
    def __init__(self, providers=None):
        super().__init__()
        self.name = '多题库回退'
        self.providers = providers or []

    def _init_tiku(self):
        active = []
        for provider in self.providers:
            try:
                provider.init_tiku()
                if not provider.DISABLE:
                    active.append(provider)
            except Exception as e:
                logger.error(f'初始化题库 {provider.name} 失败: {e}')
        self.providers = active
        if not self.providers:
            logger.error('多题库回退初始化失败: 没有可用题库')
            self.DISABLE = True
        else:
            logger.info(f"多题库回退已启用，查询顺序: {', '.join([p.__class__.__name__ for p in self.providers])}")

    def _query(self, q_info:dict) -> Optional[str]:
        for provider in self.providers:
            try:
                answer = provider._query(q_info)
            except Exception as e:
                provider_id = f'{provider.name}({provider.__class__.__name__})'
                logger.exception(f'{self.name} 查询时 {provider_id} 异常: {e}')
                continue
            if not answer:
                logger.info(f'{provider.name} 未命中，回退到下一个题库')
                continue

            # 若当前题库返回答案但类型不符，则继续回退。
            if check_answer(answer, q_info['type'], provider):
                logger.info(f'{provider.name} 命中答案')
                return answer

            logger.info(f'{provider.name} 返回答案类型不符，回退到下一个题库')
        return None

    def check_llm_connection(self) -> bool:
        for provider in self.providers:
            if not provider.check_llm_connection():
                logger.error(f'{provider.name} 连接检查失败')
                return False
        return True


# 按照以下模板实现更多题库

class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0   # token队列计数器
        self._times = 100   # 查询次数剩余, 初始化为100, 查询后校对修正

    def _query(self,q_info:dict):
        params = {
            'question': q_info['title'],
            'token': self._token,
        }
        # 同时传递 title、options、type 以提高匹配率
        if q_info.get('title'):
            params['title'] = q_info['title']
        if q_info.get('options'):
            options_val = q_info['options']
            if isinstance(options_val, list):
                options_val = '\n'.join(options_val)
            params['options'] = options_val
        if q_info.get('type'):
            params['type'] = q_info['type']

        res = requests.get(
            self.api,
            params=params,
            verify=False,
            timeout=20,
        )
        if res.status_code == 200:
            res_json = res.json()
            if not res_json['code']:
                # 如果是因为TOKEN次数到期, 则更换token
                if self._times == 0 or '次数不足' in res_json['data']['answer']:
                    logger.info(f'TOKEN查询次数不足, 将会更换并重新搜题')
                    self._token_index += 1
                    self.load_token()
                    # 重新查询
                    return self._query(q_info)
                logger.error(f'{self.name}查询失败:\n\t剩余查询数{res_json["data"].get("times",f"{self._times}(仅参考)")}:\n\t消息:{res_json["message"]}')
                return None
            self._times = res_json["data"].get("times",self._times)
            return res_json['data']['answer'].strip()
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def load_token(self):
        token_list = self._conf['tokens'].split(',')
        if self._token_index == len(token_list):
            # TOKEN 用完
            logger.error('TOKEN用完, 请自行更换再重启脚本')
            raise PermissionError(f'{self.name} TOKEN 已用完, 请更换')
        self._token = token_list[self._token_index]

    def _init_tiku(self):
        self.load_token()

class TikuGo(Tiku):
    # GO题（网课小工具题库）实现
    def __init__(self) -> None:
        super().__init__()
        self.name = 'GO题（网课小工具题库）'
        self.api = 'https://q.icodef.com/wyn-nb?v=4'
        self._headers = {
            'Authorization': '',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        self._request_lock = threading.Lock()
        self._last_request_time = 0.0
        self._min_interval = 1.0
        self._retry_times = 3
        self._retry_backoff = 1.2

    def _sleep_for_next_request(self) -> None:
        with self._request_lock:
            now = time.time()
            wait_time = max(0.0, self._last_request_time + self._min_interval - now)
            self._last_request_time = now + wait_time
        if wait_time > 0:
            time.sleep(wait_time)

    def _mark_request_finished(self) -> None:
        with self._request_lock:
            self._last_request_time = time.time()

    def _request_question(self, question: str, attempt: int) -> Optional[requests.Response]:
        try:
            self._sleep_for_next_request()
            res = requests.post(
                self.api,
                data={'question': question},
                headers=self._headers,
                verify=True,
                timeout=15
            )
            self._mark_request_finished()
            return res
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name}查询异常 ({attempt}/{self._retry_times}): {e}')
            self._mark_request_finished()
            return None

    def _parse_response(self, res: requests.Response) -> Optional[dict]:
        if res.status_code != 200:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应: {res.text}')
            return None

        try:
            res_json = res.json()
        except ValueError:
            logger.error(f'{self.name}查询失败: 返回内容不是有效JSON, 响应: {res.text}')
            return None

        try:
            code = int(str(res_json.get('code', '')).strip())
        except ValueError:
            code = 0

        answer = str(res_json.get('data', '')).strip()
        msg = str(res_json.get('msg', '')).strip()
        raw_text = f'{answer} {msg}'
        is_throttled = any(key in raw_text for key in ['流控限制', '速度太快', '并发限制', '忙不过来'])
        return {
            'code': code,
            'answer': answer,
            'msg': msg,
            'is_throttled': is_throttled,
        }

    def _sleep_retry(self, attempt: int, reason: str, include_min_interval: bool = False) -> None:
        if include_min_interval:
            sleep_seconds = max(self._min_interval, self._retry_backoff * attempt)
        else:
            sleep_seconds = self._retry_backoff * attempt
        logger.warning(f'{self.name}{reason}，{sleep_seconds:.1f}s 后重试 ({attempt}/{self._retry_times})')
        time.sleep(sleep_seconds)

    @staticmethod
    def _is_placeholder_answer(answer: str, msg: str) -> bool:
        return '李恒雅' in answer or '李恒雅' in msg

    def _query(self, q_info: dict):
        title = q_info.get('title', '')
        candidates = [
            title,
            re.sub(r'^【[^】]+】\s*', '', title).strip(),
            re.sub(r'^\[[^\]]+\]\s*', '', title).strip(),
        ]
        seen = set()
        normalized_titles = []
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                normalized_titles.append(item)

        for query_title in normalized_titles:
            answer = self._query_once(query_title)
            if answer:
                return answer
        return None

    def _query_once(self, question: str) -> Optional[str]:
        for attempt in range(1, self._retry_times + 1):
            res = self._request_question(question, attempt)
            if res is None:
                if attempt < self._retry_times:
                    self._sleep_retry(attempt, '查询异常', include_min_interval=True)
                    continue
                break

            parsed = self._parse_response(res)
            if not parsed:
                return None

            code = parsed['code']
            answer = parsed['answer']
            msg = parsed['msg']
            is_throttled = parsed['is_throttled']

            if code != 1:
                if is_throttled and attempt < self._retry_times:
                    self._sleep_retry(attempt, '触发流控')
                    continue
                logger.info(f"{self.name}未命中或失败: {msg or '未知错误'}")
                return None

            if not answer:
                return None

            # GO题库在未搜到时可能在 data/msg 中返回“李恒雅正在努力撰写中...”。
            if self._is_placeholder_answer(answer, msg):
                if is_throttled and attempt < self._retry_times:
                    self._sleep_retry(attempt, '命中流控提示')
                    continue
                return None

            return answer

        return None

    def _init_tiku(self):
        self._headers['Authorization'] = self._conf.get('go_authorization', self._headers['Authorization'])
        try:
            min_interval = float(self._conf.get('go_min_interval', self._min_interval))
            if min_interval < 0:
                raise ValueError('go_min_interval must be non-negative')
            self._min_interval = min_interval
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_min_interval 无效，使用默认值 {self._min_interval}')

        try:
            retry_times = int(self._conf.get('go_retry_times', self._retry_times))
            if retry_times < 1:
                raise ValueError('go_retry_times must be >= 1')
            self._retry_times = retry_times
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_retry_times 无效，使用默认值 {self._retry_times}')

        try:
            retry_backoff = float(self._conf.get('go_retry_backoff', self._retry_backoff))
            if retry_backoff < 0:
                raise ValueError('go_retry_backoff must be non-negative')
            self._retry_backoff = retry_backoff
        except (TypeError, ValueError):
            logger.warning(f'{self.name}配置 go_retry_backoff 无效，使用默认值 {self._retry_backoff}')

class TikuLike(Tiku):
    # LIKE知识库实现 参考 https://www.datam.site/
    def __init__(self) -> None:
        super().__init__()
        self.name = 'LIKE知识库'
        self.ver = '2.0.0' #对应官网API版本
        self.query_api = 'https://app.datam.site/api/v1/query'
        self.models_api = 'https://app.datam.site/api/v1/query/models'
        self.balance_api = 'https://app.datam.site/api/v1/balance'
        self.homepage = 'https://www.datam.site'
        self._model = None
        self._timeout = 300
        self._retry = True
        self._retry_times = 3
        self._tokens = []
        self._balance = {}
        self._search = False
        self._vision = True
        self._count = 0
        self._headers = {"Content-Type": "application/json"}

    def _query(self, q_info:dict = None):
        if not q_info:
            logger.error("当前无题目信息，请检查")
            return ""
        
        q_info_map = {"single": "【单选题】", "multiple": "【多选题】", "completion": "【填空题】", "judgement": "【判断题】"}
        q_info_prefix = q_info_map.get(q_info['type'], "【其他类型题目】")
        options = ', '.join(q_info['options']) if isinstance(q_info['options'], list) else q_info['options']
        question = f"{q_info_prefix}{q_info['title']}\n"

        if q_info['type'] in ['single', 'multiple']:
            question += f"选项为: {options}\n"

        # 随机选择一个token进行查询
        token = random.choice(self._tokens)
        
        # 检查该token是否有余额
        if self._balance.get(token, 0) <= 0:
            logger.error(f'{self.name}当前Token查询次数不足: ...{token[-5:]}')
            # 尝试选择其他有余额的token
            available_tokens = [t for t in self._tokens if self._balance.get(t, 0) > 0]
            if available_tokens:
                token = random.choice(available_tokens)
            else:
                logger.error(f'{self.name}所有Token查询次数都不足')
                return None

        ans = None
        try_times = 0
        
        # 尝试查询，直到成功或达到重试次数
        while not ans and self._retry and try_times < self._retry_times:
            ans = self._query_single(token, question)
            try_times += 1
            if ans:  # 如果查询成功，减少余额
                self._balance[token] -= 1
                logger.info(f'使用Token ...{token[-5:]} 查询成功，剩余次数: {self._balance[token]}')
                break
            elif try_times < self._retry_times:
                logger.warning(f'使用Token ...{token[-5:]} 查询失败，进行第 {try_times + 1} 次重试...')
        
        # 10次查询后更新余额
        self._count = (self._count + 1) % 10
        if self._count == 0:
            self.update_times()

        return ans
    
    def _query_single(self, token: str = "", query: str = "") -> str:
        """
        查询单个问题的答案
        
        Args:
            token: API访问令牌
            query: 查询的问题内容
            
        Returns:
            查询到的答案，如果失败则返回None
        """
        # 验证输入参数
        if not token:
            logger.error(f'{self.name}查询失败: 未提供有效的token')
            return None
        
        if not query:
            logger.error(f'{self.name}查询失败: 查询内容为空')
            return None
            
        # 设置请求头
        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'
        
        # 准备请求数据
        request_data = {
            'query': query,
            'model': self._model if self._model else '',
            'search': self._search,
            'vision': self._vision
        }
        
        # 发送API请求
        try:
            res = requests.post(
                self.query_api,
                json=request_data,
                headers=temp_headers,
                verify=False,
                timeout=self._timeout  # 添加超时设置
            )
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}查询超时: 请求超过300秒')
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到API服务器')
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name}查询异常: \n{e}')
            return None
        except Exception as e:
            logger.error(f'{self.name}查询发生未知错误: \n{e}')
            return None

        # 处理HTTP响应
        if res.status_code == 200:
            return self._parse_response(res)
        elif res.status_code == 401:
            logger.error(f'{self.name}认证失败: 请检查Token是否正确或已过期')
        elif res.status_code == 429:
            logger.error(f'{self.name}请求过于频繁: 已达到API速率限制')
        elif res.status_code == 500:
            logger.error(f'{self.name}服务器内部错误: API服务暂时不可用')
        elif res.status_code == 400:
            logger.error(f'{self.name}请求参数错误: 请检查查询内容格式')
        elif res.status_code == 403:
            logger.error(f'{self.name}访问被拒绝: 可能是Token权限不足')
        else:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应内容: \n{res.text}')
        
        return None
    
    def _parse_response(self, response):
        """
        解析API响应
        
        Args:
            response: HTTP响应对象
            
        Returns:
            解析后的答案，如果解析失败则返回None
        """
        try:
            res_json = response.json()
        except json.JSONDecodeError:
            logger.error(f'{self.name}响应解析失败: 响应不是有效的JSON格式')
            return None
        except Exception as e:
            logger.error(f'{self.name}响应解析异常: {e}')
            return None
        
        # 记录响应消息
        msg = res_json.get('message', '')
        if msg:
            logger.info(f'{self.name}响应消息: {msg}')

        results = res_json.get('results', {})
        if not results or not isinstance(results, dict):
            logger.error(f'{self.name}查询结果格式错误: API返回结果中results字段格式不正确')
            return None
            
        output = results.get('output', None)
        if output is None or not isinstance(output, dict):
            logger.error(f'{self.name}查询结果中output字段格式错误或不存在')
            return None
            
        q_type = output.get('questionType', None)
        if q_type is None:
            logger.error(f'{self.name}查询结果中questionType字段不存在')
            return None
            
        answer = output.get('answer', None)
        if answer is None:
            logger.error(f'{self.name}查询结果中answer字段不存在')
            return None
            
        # 根据题目类型提取答案
        return self._extract_answer_by_type(q_type, answer)
    
    def _extract_answer_by_type(self, q_type: str, answer: dict) -> str:
        """
        根据题目类型提取答案
        
        Args:
            q_type: 题目类型
            answer: 答案字典
            
        Returns:
            提取的答案文本
        """
        if not isinstance(answer, dict):
            logger.error(f'{self.name}答案格式错误: 不是有效的字典格式')
            return None
            
        if q_type == "CHOICE":
            selected_options = answer.get('selectedOptions', None)
            if selected_options is not None:
                if isinstance(selected_options, list) and selected_options:
                    # 过滤掉None和空字符串
                    valid_options = [opt for opt in selected_options if opt is not None and str(opt).strip()]
                    if valid_options:
                        return '\n'.join(str(opt) for opt in valid_options)
                    else:
                        logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
                else:
                    logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
            else:
                logger.error(f'{self.name}CHOICE类型题目缺少selectedOptions字段')
        elif q_type == "FILL_IN_BLANK":
            blanks = answer.get('blanks', None)
            if blanks is not None:
                if isinstance(blanks, list) and blanks:
                    # 过滤掉None和空字符串
                    valid_blanks = [blank for blank in blanks if blank is not None and str(blank).strip()]
                    if valid_blanks:
                        return "\n".join(str(blank) for blank in valid_blanks)
                    else:
                        logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
                else:
                    logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
            else:
                logger.error(f'{self.name}FILL_IN_BLANK类型题目缺少blanks字段')
        elif q_type == "JUDGMENT":
            is_correct = answer.get('isCorrect', None)
            if is_correct is not None:
                return "正确" if is_correct else "错误"
            else:
                logger.error(f'{self.name}JUDGMENT类型题目缺少isCorrect字段')
        else:
            otherText = answer.get('otherText', None)
            if otherText is not None:
                return str(otherText)
            else:
                logger.error(f'{self.name}未知题目类型{q_type}且缺少otherText字段')
        
        return None
    
    def get_api_balance(self, token:str = ""):
        if not token:
            logger.error(f'{self.name}获取余额失败: 未提供有效的token')
            return 0
            
        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'
        try:
            res = requests.get(
                self.balance_api,
                headers=temp_headers,
                verify=False,
                timeout=self._timeout
            )
            if res.status_code == 200:
                res_json = res.json()
                return int(res_json.get("balance", 0))
            else:
                logger.error(f'{self.name}请求余额接口失败，状态码: {res.status_code}')
                return 0
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}获取余额超时: 请求超过30秒')
            return 0
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到余额查询API服务器')
            return 0
        except ValueError:  # json解析错误或int转换错误
            logger.error(f'{self.name}余额响应解析失败: 响应格式不正确')
            return 0
        except Exception as e:
            logger.error(f'{self.name}Token余额查询过程中出现错误: {e}')
            return 0

    def update_times(self) -> None:
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何Token, 无法更新余额')
            return
        for token in self._tokens:
            balance = self.get_api_balance(token)
            self._balance[token] = balance
            logger.info(f"当前LIKE知识库Token: ...{token[-5:]} 的剩余查询次数为: {balance} (仅供参考, 实际次数以查询结果为准)")

    def load_tokens(self) -> None:
        tokens_str = self._conf.get('tokens')
        if not tokens_str:
            logger.error(f'{self.name}配置中未找到tokens')
            self._tokens = []
            return
        if ',' in tokens_str:
            tokens = [token.strip() for token in tokens_str.split(',') if token.strip()]
        else:
            tokens = [tokens_str.strip()] if tokens_str.strip() else []
        self._tokens = tokens
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何有效的Token')

    def load_config(self) -> None:
        # 从配置中获取参数，提供默认值
        self._search = self._conf.get('likeapi_search', False)
        self._model = self._conf.get('likeapi_model', None)
        self._vision = self._conf.get('likeapi_vision', True)
        self._retry = self._conf.get("likeapi_retry", True)
        self._retry_times = int(self._conf.get("likeapi_retry_times", 3))

    def _init_tiku(self) -> None:
        self.load_config()
        self.load_tokens()
        if self._tokens:
            self.update_times()
        else:
            logger.error(f'{self.name}初始化失败: 未加载任何有效的Token')
            self.DISABLE = True

class TikuAdapter(Tiku):
    # TikuAdapter题库实现 https://github.com/DokiDoki1103/tikuAdapter
    def __init__(self) -> None:
        super().__init__()
        self.name = 'TikuAdapter题库'
        self.api = ''

    def _query(self, q_info: dict):
        # 判断题目类型
        if q_info['type'] == "single":
            type = 0
        elif q_info['type'] == 'multiple':
            type = 1
        elif q_info['type'] == 'completion':
            type = 2
        elif q_info['type'] == 'judgement':
            type = 3
        else:
            type = 4

        options = q_info['options']
        res = requests.post(
            self.api,
            json={
                'question': q_info['title'],
                'options': [sub(r'^[A-Za-z]\.?、?\s?', '', option) for option in options.split('\n')],
                'type': type
            },
            verify=False,
            timeout=20,
        )
        if res.status_code == 200:
            res_json = res.json()
            # if bool(res_json['plat']):
            # plat无论搜没搜到答案都返回0
            # 这个参数是tikuadapter用来设定自定义的平台类型
            if not len(res_json['answer']['bestAnswer']):
                logger.error("查询失败, 返回：" + res.text)
                return None
            sep = "\n"
            return sep.join(res_json['answer']['bestAnswer']).strip()
        # else:
        #   logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def _init_tiku(self):
        # self.load_token()
        self.api = self._conf['url']


class TikuXiaomiMiMo(Tiku):
    """小米 MiMo 大模型答题实现（OpenAI 兼容协议）"""

    # 自动选模型时的关键词优先级（越靠前越优先）
    DEFAULT_MODEL_PRIORITY = ('deepseek', 'glm', 'kimi', 'hunyuan', 'minimax', 'hy')
    # 不适合答题场景的模型关键词（嵌入/向量/视觉/图像）
    EXCLUDE_MODEL_KEYWORDS = ('embed', 'rerank', 'image', 'vision', 'tts', 'whisper', 'ocr')

    def __init__(self):
        super().__init__()
        self.name = 'Xiaomi MiMo'
        self._api_url = ''
        self._token = ''
        self._model = ''
        self._timeout = 60
        self._max_tokens = 1024

    def _init_tiku(self):
        self._api_url = self._conf.get('url', '').rstrip('/')
        self._token = self._conf.get('tokens', '')
        self._model = self._conf.get('model', '').strip()

        if not self._api_url:
            logger.error(f'{self.name} 未配置API地址（url）')
            self.DISABLE = True
            return
        if not self._token:
            logger.error(f'{self.name} 未配置API Token（tokens）')
            self.DISABLE = True
            return

        # 拼接 OpenAI 兼容的 chat completions 端点
        # 兼容三种配置写法：
        #   http://host:8790          -> http://host:8790/v1/chat/completions
        #   http://host:8790/v1       -> http://host:8790/v1/chat/completions
        #   http://host:8790/v1/chat/completions -> 保持原样
        if not self._api_url.endswith('/chat/completions'):
            if not re.search(r'/v\d+$', self._api_url):
                self._api_url = self._api_url.rstrip('/') + '/v1'
            self._api_url = self._api_url.rstrip('/') + '/chat/completions'

        # 模型为 auto 时自动向服务端获取可用模型
        if not self._model or self._model.lower() in ('auto', 'none', '-'):
            self._model = self._auto_select_model() or ''

        if not self._model:
            logger.error(f'{self.name} 无法确定可用模型，请在 config.ini 的 [tiku] model 中手动填写')
            self.DISABLE = True
            return

        logger.info(f'{self.name} 初始化完成: url={self._api_url}, model={self._model}')

    def _models_url(self) -> str:
        """由 chat completions 端点推导出 /v1/models 端点"""
        return re.sub(r'/chat/completions$', '/models', self._api_url)

    def fetch_models(self) -> list:
        """
        向服务端请求可用模型列表，返回模型 id 的 list。
        兼容 OpenAI 标准的 {"data": [{"id": ...}]} 以及 {"models": [...]} 两种返回格式。
        失败时返回空 list。
        """
        models_url = self._models_url()
        headers = {'Authorization': f'Bearer {self._token}'}
        try:
            res = requests.get(models_url, headers=headers, timeout=15, verify=False)
        except requests.exceptions.RequestException as e:
            logger.warning(f'{self.name} 获取模型列表失败: {e}')
            return []

        if res.status_code != 200:
            logger.warning(f'{self.name} 获取模型列表失败: status={res.status_code}, body={res.text[:200]}')
            return []

        try:
            data = res.json()
        except ValueError:
            logger.warning(f'{self.name} 模型列表响应不是有效JSON: {res.text[:200]}')
            return []

        raw = data.get('data') if isinstance(data, dict) else None
        if raw is None and isinstance(data, dict):
            raw = data.get('models')
        if not isinstance(raw, list):
            logger.warning(f'{self.name} 模型列表格式无法识别: {str(data)[:200]}')
            return []

        ids = []
        for item in raw:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict) and item.get('id'):
                ids.append(item['id'])
        return ids

    def _auto_select_model(self) -> Optional[str]:
        """
        自动选择模型，优先级：
          1. [tiku] model_priority 中按顺序最先匹配到的
          2. 配置中的 model_prefer（若存在且可用）
          3. 内置关键词优先级（跳过嵌入/向量/视觉等不适合答题的模型）
          4. 列表中的第一个模型
        """
        models = self.fetch_models()
        if not models:
            logger.warning(f'{self.name} 自动获取模型失败，服务端未返回可用模型列表')
            return None

        logger.info(f'{self.name} 服务端可用模型({len(models)}): {", ".join(models)}')

        # 1. 显式优先级配置
        priority_raw = (self._conf.get('model_priority') or '').strip()
        if priority_raw:
            for want in [m.strip() for m in priority_raw.split(',') if m.strip()]:
                for mid in models:
                    if mid == want:
                        logger.info(f'{self.name} 按 model_priority 选中模型: {mid}')
                        return mid

        # 2. 单独的偏好配置
        prefer = (self._conf.get('model_prefer') or '').strip()
        if prefer and prefer in models:
            logger.info(f'{self.name} 按 model_prefer 选中模型: {prefer}')
            return prefer

        # 3. 内置关键词优先级
        for kw in self.DEFAULT_MODEL_PRIORITY:
            for mid in models:
                low = mid.lower()
                if kw in low and not any(b in low for b in self.EXCLUDE_MODEL_KEYWORDS):
                    logger.info(f'{self.name} 按关键词"{kw}"自动选中模型: {mid}')
                    return mid

        # 4. 兜底：第一个非排除项
        for mid in models:
            low = mid.lower()
            if not any(b in low for b in self.EXCLUDE_MODEL_KEYWORDS):
                logger.info(f'{self.name} 默认选中模型: {mid}')
                return mid

        logger.warning(f'{self.name} 模型列表中无非排除项可用: {", ".join(models)}')
        return None

    def _query(self, q_info: dict) -> Optional[str]:
        prompt = self._build_prompt(q_info)

        headers = {
            'Authorization': f'Bearer {self._token}',
            'content-type': 'application/json',
        }
        body = {
            'model': self._model,
            'max_tokens': self._max_tokens,
            'messages': [{'role': 'user', 'content': prompt}],
        }

        try:
            res = requests.post(
                self._api_url,
                json=body,
                headers=headers,
                verify=False,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout:
            logger.error(f'{self.name} 请求超时')
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name} 连接失败，请检查网络或API地址')
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name} 请求异常: {e}')
            return None

        if res.status_code != 200:
            logger.error(f'{self.name} 请求失败: status={res.status_code}, body={res.text[:500]}')
            return None

        try:
            res_json = res.json()
        except ValueError:
            logger.error(f'{self.name} 响应不是有效JSON: {res.text[:500]}')
            return None

        # OpenAI 兼容格式: {"choices": [{"message": {"content": "..."}}]}
        choices = res_json.get('choices', [])
        if not choices or not isinstance(choices, list):
            logger.error(f'{self.name} 响应中无 choices 字段: {res_json}')
            return None

        raw_text = choices[0].get('message', {}).get('content', '').strip()
        if not raw_text:
            logger.error(f'{self.name} 响应内容为空')
            return None

        answer = self._extract_answer(raw_text, q_info['type'])
        logger.debug(f'{self.name} 原始回复: {raw_text[:200]} -> 提取答案: {answer}')
        return answer

    def _build_prompt(self, q_info: dict) -> str:
        q_type = q_info['type']
        title = q_info['title']
        options = q_info.get('options', '')

        type_name_map = {
            'single': '单选题',
            'multiple': '多选题',
            'judgement': '判断题',
            'completion': '填空题',
        }
        type_name = type_name_map.get(q_type, '题目')

        prompt = f"请回答以下{type_name}，只给出答案，不要解释过程。\n\n题目：{title}\n"

        if q_type in ('single', 'multiple') and options:
            if isinstance(options, list):
                options = '\n'.join(options)
            prompt += f"\n选项：\n{options}\n"

        if q_type == 'single':
            prompt += "\n请只回答一个选项字母（如 A）。"
        elif q_type == 'multiple':
            prompt += "\n请回答所有正确选项的字母，用逗号分隔（如 A,B,C）。"
        elif q_type == 'judgement':
            prompt += "\n请只回答 正确 或 错误。"
        elif q_type == 'completion':
            prompt += "\n请只给出填空的答案，多个空用换行分隔。"

        return prompt

    @staticmethod
    def _extract_answer(raw_text: str, q_type: str) -> str:
        """从大模型回复中提取干净的答案"""
        text = raw_text.strip()

        if q_type == 'single':
            # 尝试提取单个选项字母: "A", "答案是A", "A. xxx" 等
            import re
            # 优先匹配独立大写字母（带词边界）
            m = re.search(r'\b([A-Za-z])\b', text)
            if m:
                return m.group(1).upper()
            # 中文+字母场景（如"答案是B"），无词边界
            m = re.search(r'[A-Za-z]', text)
            if m:
                return m.group(0).upper()
            # 如果没有字母，直接返回第一行
            return text.split('\n')[0].strip()

        elif q_type == 'multiple':
            import re
            # 尝试收集所有选项字母
            # 先看是否已有分隔符（逗号、换行等）
            if re.search(r'[A-Za-z]\s*[,，、\n]\s*[A-Za-z]', text):
                # 已有分隔格式，提取所有字母
                letters = sorted(set(re.findall(r'[A-Za-z]', text)))
                return ','.join(l.upper() for l in letters)
            # 连续字母如 "ABCD"
            m = re.search(r'\b([A-Za-z]{2,})\b', text)
            if m:
                letters = sorted(set(m.group(1).upper()))
                return ','.join(letters)
            # 收集所有单字母
            letters = sorted(set(re.findall(r'\b([A-Za-z])\b', text)))
            if letters:
                return ','.join(l.upper() for l in letters)
            return text.split('\n')[0].strip()

        elif q_type == 'judgement':
            if '正确' in text or '对' in text:
                return '正确'
            elif '错误' in text or '错' in text:
                return '错误'
            return text.split('\n')[0].strip()

        elif q_type == 'completion':
            # 去掉可能的前缀说明
            import re
            # 去掉 "答案：" 等前缀
            text = re.sub(r'^(答案[：:]\s*)', '', text)
            return text.strip()

        return text.split('\n')[0].strip()

    def check_llm_connection(self) -> bool:
        """检查大模型API连接是否可用"""
        if not self._api_url or not self._token:
            return False
        headers = {
            'Authorization': f'Bearer {self._token}',
            'content-type': 'application/json',
        }
        body = {
            'model': self._model,
            'max_tokens': 10,
            'messages': [{'role': 'user', 'content': 'ping'}],
        }
        try:
            res = requests.post(
                self._api_url,
                json=body,
                headers=headers,
                verify=False,
                timeout=15,
            )
            if res.status_code == 200:
                logger.info(f'{self.name} 连接检查通过')
                return True
            else:
                logger.error(f'{self.name} 连接检查失败: status={res.status_code}, body={res.text[:300]}')
                return False
        except Exception as e:
            logger.error(f'{self.name} 连接检查异常: {e}')
            return False


PROVIDER_REGISTRY = {
    'TikuYanxi': TikuYanxi,
    'TikuGo': TikuGo,
    'TikuLike': TikuLike,
    'TikuAdapter': TikuAdapter,
    'Xiaomi MiMo': TikuXiaomiMiMo,
    'DeepSeek': TikuXiaomiMiMo,
}
