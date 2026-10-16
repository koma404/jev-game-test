# 部署到 VPS（香港 / 日本 / 新加坡）

适合：想让国内朋友不开代理也能稳定访问、地址固定、电脑不用一直开着。

## 0. 租机器时选

- 机房：香港优先，其次日本、新加坡；系统选 **Ubuntu 22.04 / 24.04** 或 **Debian 12**。
- 配置：最低档就够（1 核、512MB–1GB 内存）。
- 记下：服务器 **IP**、**root 密码**（或 SSH 密钥）。

## 1. 把代码传上去（在你的 Windows 电脑上）

Windows 10/11 自带 `scp`。打开命令行：

```
scp -r D:\jev-game-test root@服务器IP:/root/
```

第一次连接会问 `Are you sure you want to continue connecting`，输入 `yes`，再输入 root 密码。
（会把整个项目连同 `key.txt` 一起传上去；服务器是你自己的，没关系。）

## 2. 登录服务器并安装

```
ssh root@服务器IP
cd /root/jev-game-test
bash deploy/install.sh
```

它会依次问：

| 问题 | 填什么 |
|---|---|
| OpenRouter key | 你的 key（输入时不显示，正常） |
| 访问口令 | 自己想一个，发给朋友用 |
| 每日 Jev 花费上限 | 直接回车 = 0.5 美元/天 |
| 端口 | 直接回车 = 80（链接里就不用写端口） |

装完会打印：`http://服务器IP/?code=口令`，发给朋友即可。

**如果打不开**：大多数商家后台有「安全组 / 防火墙」，要在那里放行 **TCP 80**（或你填的端口）。

## 3. 以后更新代码

在 Windows 上拉取新代码后，再传一次、再更新一次（key、口令等设置会保留）：

```
scp -r D:\jev-game-test root@服务器IP:/root/
ssh root@服务器IP "cd /root/jev-game-test && bash deploy/install.sh --update"
```

## 常用命令（登录服务器后）

```
systemctl status twelve                        # 服务状态
journalctl -u twelve -f                        # 实时日志，Ctrl+C 退出
nano /etc/twelve/env && systemctl restart twelve   # 改 key / 口令 / 每日上限
systemctl stop twelve                          # 暂停对外服务
```

## 安全

- 再强调一次：去 OpenRouter 后台给这把 key 设额度上限。服务里的每日上限存在 `/var/lib/twelve/budget.json`，重启不清零，但它只是第二道闸。
- 设置文件 `/etc/twelve/env` 只有 root 能读；服务以低权限用户 `twelve` 运行，只能写 `/var/lib/twelve`。
- 走的是 http（没有 https）：口令在网络上是明文。演示用够了；要 https 需要域名，而大陆访问用域名又牵扯备案，先不做。
