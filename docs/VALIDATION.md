# 验证记录

**中文** | [English](../docs_en/VALIDATION.md)

本页说明离线演示与合成测试能够验证的行为。

## 复核命令

需要 Python 3.11+、POSIX 文件锁、SQLite FTS5 和 Europe/London 时区数据：

```sh
python3 -B scripts/demo.py
python3 -B scripts/check.py
```

演示与测试仅使用内存协议夹具和临时文件。不启动 app-server，不连接本机 socket，不访问生产配置或记忆，也不触发模型调用。

## 当前检查范围

| 机制 | 证据位置 | 检查目的 |
| --- | --- | --- |
| 记忆日与 DST | `tests/test_time_window.py` | 日历边界、23/25 小时日期和归档截止条件 |
| L2 原生与旧格式 | `tests/test_domain_l2.py` | 严格格式、部分覆盖来源、原始字节和不确定时间 |
| 日文件提交与恢复 | `tests/test_storage_reconcile.py` | 幂等性、状态失败后恢复、路径及暂存文件处理 |
| 归档包 | `tests/test_archive.py` | 可重复生成、字节保留、篡改及不安全成员拒绝 |
| 检索与只读性 | `tests/test_index_recall.py` | 中英文词法、预算、时间精度、索引缺失/未就绪行为 |
| 按日期读取 | `tests/test_date_first_showcase.py` | 正文筛选、缓存、超限减半、变化后的有限重试及人格检查 |

## 已验证范围

- `scripts/demo.py` 验证每日幂等提交、中文召回、索引读取后字节不变、单日期归档验证与 23 小时的春季时钟切换日；临时数据自动清理。
- `scripts/check.py` 执行 **51 项测试，全部通过，0 跳过**。其中 46 项来自所选原工程测试，5 项为合成 RPC 日期读取测试。

## 验证边界

这些检查不能证明当前生产调度健康、真实历史完整、并发进程行为覆盖充分，或所有 Codex 版本兼容。未包含完整生产流水线、完整月度归档/索引协调、CLI/MCP、跨人格 fleet 或部署验收。合成源使用既定 RPC 契约，未进行真实协议探测。
