# 第56轮：本机Harbor Cursor环境准备

日期：2026-11-06。用户要求快速准备现成Cursor CLI执行环境。

检查Docker、Harbor0.25.0、宿主CLI；在一次性amd64题目容器中成功安装并启动官方Cursor CLI2026.11.05-86047df。复制缓存fix-git生成独立联网派生任务并固定指纹，同环境Oracle1/1通过，退出0，无残留容器。

新增单题配置与隐藏输入用户Key的启动脚本，支持先列模型、再指定模型运行；单并发、15分钟执行期限、内部重试0，沿用现有证据归档。说明见[快速开始](../cursor-local-quickstart.md)。

用户表示需要创建Key；当前进程没有CURSOR_API_KEY。未读取账户密钥或调用模型，真实Cursor执行与账户额度尚待验证。宿主status的Keychain错误不等同于编辑器未登录。

验收：派生任务指纹校验、脚本help、全仓ruff check／format通过；真实Oracle作为环境验证。本轮未改变核心执行逻辑，未新增单元测试。
