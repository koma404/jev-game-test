# 加密的运行日志存档

Render 每次部署都会清空日志，所以按期下载 `/admin/log` 后合并去重、加密存到这里（仓库是公开的，日志里有玩家原话，不能明文）。
口令不在仓库里，由项目负责人保管。

解密：`openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in log_日期.jsonl.enc -out log.jsonl`
加密：`openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in log.jsonl -out log_日期.jsonl.enc`
统计：解密后 `python -m twelve.stats log.jsonl`
