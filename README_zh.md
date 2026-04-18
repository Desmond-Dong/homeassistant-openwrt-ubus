# Home Assistant 的 OpenWrt ubus 集成

[English Version](README.md) | **中文版本**

`openwrt_ubus` 是一个面向 OpenWrt 路由器的 Home Assistant 自定义集成。它通过 ubus JSON-RPC 连接 OpenWrt，并在 Home Assistant 中提供路由器状态、AP 与客户端信息、可选有线设备跟踪、服务控制，以及一个集成内置的拓扑页面。

## 简介

这个 fork 主要聚焦在稳定、实用的日常监控与控制：

- 路由器系统传感器
- AP 与 STA 传感器
- 可选 QModem 传感器
- 可选网络接口传感器
- 每个客户端独立的 `device_tracker`
- 可选的有线客户端跟踪
- 服务开关与控制按钮
- 无线客户端踢除按钮
- 集成原生拓扑页面

拓扑页面不是 Lovelace 卡片，而是由集成本身注册的独立页面。你可以从集成选项菜单打开，或者直接访问 `/openwrt-ubus-topology`。

## 功能

### 设备跟踪

- 通过 `iwinfo` 或 `hostapd` 跟踪无线客户端
- 启用后也可以根据 ARP 和邻居信息跟踪有线客户端
- 为每个客户端 MAC 创建 Home Assistant `device_tracker` 实体
- 通过 `via_device` 保留 AP 层级关系
- 客户端命名顺序为：主机名、客户端 IP、MAC
- 对于刚掉线的无线客户端，会优先保留为离线无线状态，避免被残留 ARP 数据立即误判为有线

### 传感器

- 系统传感器：运行时间、负载、内存、板级信息、温度、conntrack、DHCP 数量等
- AP 传感器：SSID、信道、频率、模式、速率、信号和质量等无线指标
- STA 传感器：每个无线客户端的 RSSI、速率、连接时长、所属 AP 等
- ETH/接口传感器：bridge、Ethernet、DSA 等网络接口状态和计数器
- 当 `modem_ctrl` 可用时提供 QModem 传感器

### 服务管理

- 从 `rc` 列出由 procd 管理的服务
- 为选中的服务创建开关实体
- 创建启动、停止、重启、启用、禁用按钮

### 无线客户端控制

- 可选的逐客户端踢除按钮，适用于 `hostapd` 管理的无线客户端
- 主要面向暴露 `hostapd.*` ubus 方法的 AP 模式接口

### 拓扑页面

- 内置图形页面，展示路由器、AP、无线客户端和有线客户端
- 对刚刚消失的客户端短时间保留为离线状态
- 主节点可点击跳转到对应的 Home Assistant 设备页
- 支持 `zeroconf` 和 `ssdp` 发现叠加层
- 协议节点挂在客户端节点后方，并使用虚线边显示
- 匹配策略偏保守：先按 IP，再按 hostname，且只接受唯一匹配

## 依赖要求

你的 OpenWrt 路由器需要通过 HTTP 或 HTTPS 提供 ubus 接口。

常见必需软件包：

```sh
opkg install rpcd uhttpd uhttpd-mod-ubus luci-app-uhttpd
```

可选软件包：

```sh
opkg install hostapd
```

必需服务：

```sh
/etc/init.d/rpcd enable
/etc/init.d/rpcd start
/etc/init.d/uhttpd enable
/etc/init.d/uhttpd start
```

为了让功能完整可用，建议集成使用的 OpenWrt 用户至少有权限：

- 调用 `system`、`iwinfo`、`hostapd.*`、`rc`、`uci`、`dhcp`、`network.device`、`luci-rpc`、`modem_ctrl` 等 ubus 对象
- 在主机名解析依赖租约文件时，可以读取 DHCP lease 文件

## 安装

### HACS

在 HACS 中将本仓库添加为自定义集成源：

`https://github.com/Desmond-Dong/homeassistant-openwrt-ubus`

然后安装 `OpenWrt ubus`，重启 Home Assistant，再到 `设置 -> 设备与服务` 中添加集成。

### 手动安装

克隆或下载本仓库，然后把 `custom_components/openwrt_ubus` 复制到 Home Assistant 的 `custom_components` 目录。

```sh
git clone https://github.com/Desmond-Dong/homeassistant-openwrt-ubus.git
```

重启 Home Assistant 后，在 `设置 -> 设备与服务` 中添加集成。

## 路由器 ACL 示例

如果主机名、文件读取或服务控制因权限不足不可用，请给路由器上的用户授予相应 ACL。

下面是一个可信管理员用户的完整权限示例：

```json
{
  "root": {
    "description": "Root user full access to ubus",
    "read": {
      "ubus": {
        "*": ["*"]
      }
    },
    "write": {
      "ubus": {
        "*": ["*"]
      }
    }
  }
}
```

将它保存到 `/usr/share/rpcd/acl.d/root.json`，然后重启 `rpcd` 和 `uhttpd`。

## 配置项

### 连接配置

| 选项 | 含义 | 默认值 |
| --- | --- | --- |
| `host` | OpenWrt 主机名或 IP | 必填 |
| `username` | 路由器用户名 | 必填 |
| `password` | 路由器密码 | 必填 |
| `use_https` | 使用 HTTPS 而不是 HTTP | `false` |
| `verify_ssl` | 校验路由器证书 | `false` |
| `cert_path` | 可选客户端证书路径 | 空 |
| `port` | 自定义 ubus Web 端口 | `80` 或 `443` |
| `endpoint` | ubus URL 路径 | `ubus` |
| `tracking_method` | 设备跟踪唯一 ID 策略 | `combined` |
| `wireless_software` | 无线数据来源 | `iwinfo` |
| `dhcp_software` | 主机名和 IP 的 DHCP/租约来源 | `dnsmasq` |

### 功能开关

| 选项 | 含义 | 默认值 |
| --- | --- | --- |
| `enable_system_sensors` | 系统/路由器传感器 | `true` |
| `enable_qmodem_sensors` | QModem 传感器 | `true` |
| `enable_sta_sensors` | 每客户端无线传感器 | `true` |
| `enable_ap_sensors` | AP 传感器 | `true` |
| `enable_eth_sensors` | 网络接口传感器 | `true` |
| `enable_service_controls` | 服务开关和控制按钮 | `false` |
| `enable_device_kick_buttons` | 无线踢除按钮 | `false` |
| `enable_wired_tracking` | 跟踪有线 LAN 客户端 | `false` |

### 超时配置

| 选项 | 含义 | 默认值 |
| --- | --- | --- |
| `system_sensor_timeout` | 系统和接口轮询超时 | `30` |
| `qmodem_sensor_timeout` | QModem 轮询超时 | `120` |
| `sta_sensor_timeout` | STA 轮询超时 | `30` |
| `ap_sensor_timeout` | AP 轮询超时 | `60` |
| `service_timeout` | 服务状态和控制超时 | `30` |

### 服务选择

启用服务控制后，请在集成选项中进入 `Services` 步骤，选择要在 Home Assistant 中创建实体的 OpenWrt 服务。

## 拓扑页面

可以通过以下方式打开拓扑页面：

- `设置 -> 设备与服务 -> OpenWrt ubus -> 配置 -> Topology`
- 或直接访问 `/openwrt-ubus-topology`

图中会显示：

- 路由器节点
- AP 节点
- 无线客户端节点
- 有线客户端节点
- 可选的 `zeroconf` 和 `ssdp` 协议发现节点

协议叠加节点仅用于展示发现信息，主客户端节点仍保持正常的设备跳转行为。

## 客户端命名说明

客户端命名逻辑刻意保持简单稳定：

- 有主机名就用主机名
- 没有主机名就用客户端自己的 IP 地址
- 再没有就回退到不带分隔符的 MAC 地址

如果你看到的仍然主要是 MAC，通常说明 Home Assistant 无法从 OpenWrt 读取 DHCP 租约或 host hints，多半是 ACL 权限不够。

## 有线跟踪说明

有线跟踪基于路由器上的 ARP 和邻居信息，因此：

- 它很有用，但没有无线站点在线数据那么权威
- 残留 ARP 项可能比真实连通状态多保留一段时间
- 这个 fork 使用了更保守的回退逻辑，尽量避免刚掉线的无线客户端被过快翻成有线

## 故障排查

### 无法连接

- 确认 `rpcd` 和 `uhttpd` 正在运行
- 确认 URL、协议、端口和 endpoint 配置正确
- 如果使用自签名 HTTPS，而你没有完整证书链，请保持 `verify_ssl` 为关闭

### 设备只显示 MAC

- 检查路由器上的 DHCP 租约和 host hints 是否可读
- 检查集成用户的 OpenWrt ACL 权限
- 确认 `dhcp_software` 与路由器实际配置一致

### 没有服务实体

- 在集成选项里启用服务控制
- 进入 `Services` 步骤，至少勾选一个服务
- 确认用户有 `rc` 的 ubus 访问权限

### 没有踢除按钮

- 安装并运行 `hostapd`
- 确认无线侧配置使用的是 `hostapd`
- 确认 `hostapd.*` ubus 方法可见且权限允许

### 拓扑页面为空或显示不全

- 先正常进入一次集成，让数据管理器启动
- 确认 AP 和客户端相关数据源已启用
- 检查 Home Assistant 日志中是否有 ubus 权限或解析错误

## 调试日志

```yaml
logger:
  default: warning
  logs:
    custom_components.openwrt_ubus: debug
    custom_components.openwrt_ubus.shared_data_manager: debug
    custom_components.openwrt_ubus.extended_ubus: debug
    custom_components.openwrt_ubus.device_tracker: debug
```

## 项目结构

```text
custom_components/openwrt_ubus/
|- __init__.py
|- config_flow.py
|- const.py
|- device_tracker.py
|- sensor.py
|- switch.py
|- button.py
|- topology.py
|- shared_data_manager.py
|- extended_ubus.py
|- ubus_client.py
|- buttons/
|- sensors/
|- frontend/
`- Ubus/
```

## 许可证

本项目使用 MPL-2.0 许可证，详见 `LICENSE`。
