# 来源与展示范围

**中文** | [English](../docs_en/PROVENANCE.md)

代码选自自建的 MQElite 工程，提供可独立阅读和离线运行的核心子集。

## 依赖范围与适配

Domain、storage、archive bundle/eligibility、index/database/tokenizer 和 recall 构成仅依赖标准库的可运行子集。Index 包导出移除了未选入的生产 source loader，避免导入完整生命周期模块。

轮询源保留原有算法，其 transport 依赖仅保留两个原有异常定义。真实 socket/process 传输实现未复制；该模块是明确标注的类型选段，不是可连接 Codex 的客户端。

选取的原工程单元测试覆盖保留机制。三份测试里的中文人格称呼统一替换为合成标签，同时保留相关分词边界断言。测试材料本身是合成内容，没有真实对话记录。

日期读取展示测试、演示、检查脚本、文档与仓库基础文件为本次新写，并不作为原样生产产物。所选源测试也不是私人项目的完整验收套件。

## 未提取的内容

不包含运行中的对话数据、认证、生产配置、真实人格标识、socket、主机路径、旧档案、Git 历史或安装产物。私人流水线、Router 权威历史适配器、source loaders、完整归档生命周期及清理、多人格 runner、真实 transport、MCP/CLI 接口均在代码选段之外。

架构与机制页依据源码和所属文档解释周边行为。这些解释不充当被省略组件的可执行替代实现。

## 上游归属

MQElite 原始 NOTICE 将其描述为参考 MemoQuasarEterna L2 公开形态与行为的独立实现，并声明没有带入旧 runtime 代码与生产数据。Codex API 名称用于互操作，这里没有复制 OpenAI runtime 实现。

所选实现依赖 Python 标准库和 SQLite FTS5；这些上游提供的功能不计作 MQElite 的自建贡献。

原工程 LICENSE 和 NOTICE 原文保存在 [SOURCE_NOTICES.md](SOURCE_NOTICES.md)，继承范围与新写材料分开说明。
