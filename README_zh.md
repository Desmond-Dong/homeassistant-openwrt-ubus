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

为 OpenWrt 系统服务提供全面的服务管理，具有实时状态监控和控制功能。

![服务控制](imgs/service_control.png)
*用于管理 OpenWrt 系统服务的服务控制开关和按钮*

#### 开关实体
- **服务开关**：开启/关闭服务并提供实时状态反馈
- **实时状态监控**：显示每个被监控服务的当前运行状态
- **批量状态更新**：使用优化的 API 调用高效监控多个服务
- **状态同步**：自动状态刷新以保持与路由器状态的一致性

#### 按钮实体
集成通过专用按钮实体提供精细的服务控制：

- **🟢 启动服务**：启动已停止的服务并提供即时状态反馈
- **🔴 停止服务**：停止正在运行的服务并优雅关闭
- **✅ 启用服务**：启用服务在系统启动时自动运行
- **❌ 禁用服务**：禁用启动时自动运行同时保持当前状态
- **🔄 重启服务**：重启正在运行的服务并最小化停机时间

**管理的服务包括：**
由 procd 管理的重要 OpenWrt 系统服务：
- `dnsmasq` - 用于网络名称解析的 DNS 和 DHCP 服务器
- `dropbear` - 用于远程访问的轻量级 SSH 服务器守护程序
- `firewall` - Netfilter 防火墙配置和管理
- `network` - 网络接口配置和路由
- `uhttpd` - 用于 LuCI 界面和 ubus 通信的 Web 服务器
- `wpad` - 用于 WPA/WPA2/WPA3 认证的无线守护程序
- `odhcpd` - DHCPv6 和 IPv6 路由器通告守护程序
- `rpcd` - 用于 ubus JSON-RPC 通信的 RPC 守护程序
- 以及更多基于您的 OpenWrt 配置的系统服务...

**服务管理功能：**
- ⚡ 对状态变化的即时响应和实时反馈
- 🔄 控制操作后的自动状态刷新
- 🛡️ 带有详细用户反馈的全面错误处理
- 📊 优化的批量 API 调用以提高性能和减少路由器负载
- 🔍 服务依赖感知以确保安全的操作顺序

---

### 🚫 高级设备管理与控制

高级设备管理功能，包括从无线网络断开不需要设备的能力。

![设备踢出控制](imgs/ap_control_kick_sta.png)
*用于断开特定无线客户端的设备踢出按钮*

#### 设备踢出功能
强制断开连接的无线设备并临时限制访问。

**设备踢出工作原理：**
1. **🔍 自动检测**：自动检测所有 AP 接口上的连接无线设备
2. **🆔 动态按钮创建**：为每个当前连接的设备创建单独的踢出按钮
3. **✅ 智能可用性**：按钮仅在以下情况下出现和运行：
   - 目标设备当前已连接且活跃
   - hostapd 服务正在运行且可通过 ubus 访问
   - 设备连接到受支持的接入点接口
   - 用户具有设备管理的适当权限
4. **⚡ 取消认证操作**：向目标设备发送 IEEE 802.11 取消认证命令
5. **🕐 临时访问禁止**：自动阻止重新连接 60 秒
6. **🔄 状态同步**：踢出操作后立即刷新设备状态

#### 连接设备概览
![连接设备](imgs/system_info_connected_devices.png)
*所有连接设备的全面概览及管理控制*

**技术要求：**
- **📡 hostapd 服务**：必须安装、运行且可通过 ubus 接口访问
- **🌐 Ubus 集成**：hostapd 必须编译时包含 ubus 支持以进行设备管理
- **🔐 用户权限**：路由器用户账户必须具有 hostapd 控制的适当 ACL 权限

**设备踢出按钮详情：**
- **实体命名**：`button.kick_[设备名称]` 或 `button.kick_[mac地址]` 便于识别
- **丰富属性**：每个按钮包括设备 MAC、主机名、AP 接口、信号强度和连接时间
- **自动隐藏行为**：目标设备断开连接时按钮自动消失
- **多 AP 支持**：不同接入点接口上设备的独立踢出控制
- **安全功能**：通过确认和日志记录防止意外踢出

**配置与设置：**
出于安全考虑，设备踢出功能默认禁用。启用方法：
1. 导航到 **设置** → **设备与服务** → **OpenWrt ubus**
2. 点击集成条目上的 **配置**
3. 启用 **设备踢出按钮** 选项
4. 保存配置并重启集成
5. 确保在路由器上正确安装和配置 hostapd

**使用场景：**
- **🔒 安全**：立即断开可疑或未授权设备
- **📶 网络管理**：通过移除空闲或有问题的连接释放带宽  
- **👨‍👩‍👧‍👦 家长控制**：临时限制特定设备的访问
- **🔧 故障排除**：强制设备重新连接以解决连接问题

---

### 🔧 UCI 配置控制（高级）

集成还通过两个 Home Assistant 服务提供对 OpenWrt UCI 配置选项的直接控制。这支持高级用例，例如按设备互联网开关、动态防火墙规则和运行时配置更改 - 全部由 Home Assistant 驱动。

#### `openwrt_ubus.uci_get`

通过 ubus 读取 UCI 选项值，并可选择将结果存储在 Home Assistant 传感器实体中。

**字段：**

| 字段 | 必需 | 描述 |
|------|------|------|
| `config` | ✓ | UCI 配置名称（例如 `firewall`、`wireless`、`dhcp`） |
| `section` | 可选 | 部分名称或类型/索引（例如 `block_user_7085c2` 或 `@rule[3]`） |
| `option` | 可选 | 要检索的选项键（例如 `enabled`） |
| `target_entity_id` | 可选 | 要更新的传感器实体 ID（例如 `sensor.block_user_7085c2_enabled`） |

**示例：将防火墙规则状态存储到传感器**

```yaml
service: openwrt_ubus.uci_get
data:
  config: firewall
  section: block_user_7085c2
  option: enabled
  target_entity_id: sensor.block_user_7085c2_enabled
```

提供 `target_entity_id` 时，集成将使用检索到的 UCI 值（例如 `"0"` 或 `"1"`）更新该实体的状态。

#### `openwrt_ubus.uci_set_commit`

设置 UCI 选项值并立即提交更改。

**字段：**

| 字段 | 必需 | 描述 |
|------|------|------|
| `config` | ✓ | UCI 配置名称 |
| `section` | ✓ | 部分名称或类型/索引 |
| `option` | ✓ | 要修改的选项键 |
| `value` | ✓ | 新值（字符串） |

**示例：启用防火墙规则（阻止 MAC 地址）**

```yaml
service: openwrt_ubus.uci_set_commit
data:
  config: firewall
  section: block_user_7085c2
  option: enabled
  value: "1"
```

**示例：禁用防火墙规则（取消阻止）**

```yaml
service: openwrt_ubus.uci_set_commit
data:
  config: firewall
  section: block_user_7085c2
  option: enabled
  value: "0"
```

#### 示例：使用防火墙规则创建按设备互联网开关

您可以将 UCI 服务与简单的自动化和模板开关相结合，创建使用基于 MAC 的防火墙规则的按设备互联网开关。

**1. 自动化以保持传感器与防火墙规则同步**

```yaml
automation:
  - alias: "Sync firewall state for user 7085C2"
    trigger:
      - platform: time_pattern
        minutes: "/1"
    action:
      - service: openwrt_ubus.uci_get
        data:
          config: firewall
          section: block_user_7085c2
          option: enabled
          target_entity_id: sensor.block_user_7085c2_enabled
```

**2. 使用 UCI 支持的传感器作为状态和 UCI 调用作为操作的模板开关**

```yaml
switch:
  - platform: template
    switches:
      user_7085c2_internet:
        friendly_name: "User Internet 70:85:C2:89:EC:74"
        # ON = 防火墙规则禁用 (0) = 互联网已允许
        value_template: >
          {{ is_state('sensor.block_user_7085c2_enabled', '0') }}
        turn_on:
          - service: openwrt_ubus.uci_set_commit
            data:
              config: firewall
              section: block_user_7085c2
              option: enabled
              value: "0"
        turn_off:
          - service: openwrt_ubus.uci_set_commit
            data:
              config: firewall
              section: block_user_7085c2
              option: enabled
              value: "1"
```

此模式可以通过调整 `section`、`sensor` 和 `switch` 名称来重复使用以添加其他防火墙规则和设备。

> **注意：** UCI 服务要求为此集成配置的 OpenWrt RPC 用户具有 ubus 权限来调用 `uci get`、`uci set` 和 `uci commit`。

---

### 🔧 高级配置与优化

#### 超时设置
根据您的网络和路由器性能微调集成性能：

- **系统传感器超时**：等待系统数据收集的时间（5-300秒）
  - *推荐*：大多数路由器 30秒，较旧硬件 60秒
- **QModem 超时**：LTE/4G/5G 调制解调器查询超时（5-300秒）  
  - *推荐*：稳定连接 30秒，信号较弱区域 120秒
- **服务超时**：服务控制操作超时（5-300秒）
  - *推荐*：本地操作 30秒，复杂服务链 60秒

#### 性能优化功能
- **智能批量 API 调用**：将多个 ubus 调用合并为单个请求以提高效率
- **高级缓存系统**：通过智能缓存失效减少冗余 API 调用
- **可配置更新间隔**：调整每种传感器类型的轮询频率以平衡数据新鲜度与系统负载
- **后台处理**：非阻塞操作确保 Home Assistant 响应性
- **内存优化**：高效的数据结构和清理确保长期稳定性

#### 软件兼容性矩阵
- **无线监控选项**： 
  - `iwinfo`：标准 OpenWrt 无线信息（兼容所有设置）
  - `hostapd`：直接 hostapd 集成（启用设备踢出功能）
- **DHCP 集成选项**： 
  - `dnsmasq`：传统 DHCP/DNS 服务器（最常见）
  - `odhcpd`：现代 DHCP 服务器，支持 IPv6
  - `none`：禁用 DHCP 监控（仅无线跟踪）
- **服务管理**：自动适应可用的 procd 管理服务

## 🔧 故障排除与支持

### 常见问题与解决方案 ⚠️

**🚫 无法连接到路由器**
- ✅ 验证路由器 IP 地址正确且可从 Home Assistant 访问
- ✅ 确认用户名和密码凭据有效
- ✅ 确保 `rpcd` 和 `uhttpd` 服务正在运行：`service rpcd status && service uhttpd status`
- ✅ 检查防火墙设置是否允许 HTTP 访问 ubus（端口 80/443）
- ✅ 测试连接性：`curl http://router_ip/ubus -d '{"jsonrpc":"2.0","method":"call","params":["00000000000000000000000000000000","session","login",{"username":"root","password":"your_password"}],"id":1}'`

**❌ 未检测到设备**
- ✅ 验证无线软件设置与您的 OpenWrt 配置匹配
- ✅ 检查 DHCP 软件设置是否对应您的 DHCP 服务器
- ✅ 确保路由器上正确配置了所选的监控方法
- ✅ 测试无线检测：`iwinfo` 或检查 hostapd 状态：`ubus call hostapd.wlan0 get_clients`
- ✅ 验证 DHCP 租约文件可访问性：`ls -la /var/dhcp.leases /tmp/dhcp.leases`

**⏰ 传感器未更新**
- ✅ 检查 Home Assistant 日志中的连接错误：`设置 → 系统 → 日志`
- ✅ 验证路由器权限允许访问系统信息
- ✅ 测试系统数据访问：`ubus call system info && ubus call system board`
- ✅ 检查 Home Assistant 和路由器之间的网络连接稳定性
- ✅ 查看集成配置中的超时设置

**🏷️ 设备显示 MAC 地址而不是主机名**
- ✅ 确保主机名解析 ACL 配置正确（请参阅 [路由器权限设置](#路由器权限设置-🔐)）
- ✅ 验证 DHCP 租约文件可访问：`/var/dhcp.leases` 或 `/tmp/dhcp.leases`
- ✅ 检查 rpcd 服务在 ACL 配置后已重启：`/etc/init.d/rpcd restart`
- ✅ 确认用户账户分配到正确的 ACL 组
- ✅ 测试文件访问：`ubus call file read '{"path":"/tmp/dhcp.leases"}'`

**🚫 设备踢出按钮不工作**
- ✅ 验证 hostapd 已安装并运行：`service hostapd status`
- ✅ 检查 hostapd ubus 集成：`ubus list | grep hostapd`
- ✅ 确保在集成配置中启用了设备踢出按钮
- ✅ 确认目标设备通过 hostapd 管理的接口连接
- ✅ 测试 hostapd 控制：`ubus call hostapd.wlan0 del_client '{"addr":"device_mac","reason":5,"deauth":true,"ban_time":60000}'`

### 调试日志与诊断 🐛

启用全面的故障排除日志记录：

```yaml
# 添加到 configuration.yaml
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

**数据流架构：**
1. **SharedDataUpdateCoordinator**：具有批量 API 优化的中央数据管理
2. **ExtendedUbus**：增强的 ubus 客户端，集成 hostapd 和错误处理
3. **平台模块**：专门的传感器/实体实现
4. **缓存层**：具有失效策略的智能缓存

**关键设计模式：**
- **协调器模式**：具有实体订阅的集中数据更新
- **工厂模式**：基于检测到的设备/服务的动态实体创建
- **观察者模式**：使用最少 API 调用的实时更新
- **策略模式**：可配置的无线/DHCP 检测方法

### 贡献指南 🤝

1. **🍴 Fork 仓库**：创建您自己的开发 fork
2. **🌿 创建功能分支**：使用描述性分支名称（`feature/device-kick-improvements`）
3. **✏️ 代码质量**：遵循 Home Assistant 开发指南
4. **🧪 彻底测试**：使用各种 OpenWrt 配置进行测试
5. **📝 记录更改**：更新 README 和代码注释
6. **📤 提交 Pull Request**：提供详细的更改描述

**开发设置：**
- 使用多个 OpenWrt 版本测试（21.02、22.03、snapshot）
- 验证与不同无线驱动程序的兼容性（ath9k、ath10k、mt76）
- 测试各种硬件平台（MIPS、ARM、x86）

**❌ 未检测到设备**
- ✅ 验证无线软件设置与您的 OpenWrt 配置匹配
- ✅ 检查 DHCP 软件设置是否对应您的 DHCP 服务器
- ✅ 确保路由器上正确配置了所选的监控方法
- ✅ 测试无线检测：`iwinfo` 或检查 hostapd 状态：`ubus call hostapd.wlan0 get_clients`
- ✅ 验证 DHCP 租约文件可访问性：`ls -la /var/dhcp.leases /tmp/dhcp.leases`

**⏰ 传感器未更新**
- ✅ 检查 Home Assistant 日志中的连接错误：`设置 → 系统 → 日志`
- ✅ 验证路由器权限允许访问系统信息
- ✅ 测试系统数据访问：`ubus call system info && ubus call system board`
- ✅ 检查 Home Assistant 和路由器之间的网络连接稳定性
- ✅ 查看集成配置中的超时设置

**🏷️ 设备显示 MAC 地址而不是主机名**
- ✅ 确保主机名解析 ACL 配置正确（请参阅 [路由器权限设置](#路由器权限设置-🔐)）
- ✅ 验证 DHCP 租约文件可访问：`/var/dhcp.leases` 或 `/tmp/dhcp.leases`
- ✅ 检查 rpcd 服务在 ACL 配置后已重启：`/etc/init.d/rpcd restart`
- ✅ 确认用户账户分配到正确的 ACL 组
- ✅ 测试文件访问：`ubus call file read '{"path":"/tmp/dhcp.leases"}'`

**🚫 设备踢出按钮不工作**
- ✅ 验证 hostapd 已安装并运行：`service hostapd status`
- ✅ 检查 hostapd ubus 集成：`ubus list | grep hostapd`
- ✅ 确保在集成配置中启用了设备踢出按钮
- ✅ 确认目标设备通过 hostapd 管理的接口连接
- ✅ 测试 hostapd 控制：`ubus call hostapd.wlan0 del_client '{"addr":"device_mac","reason":5,"deauth":true,"ban_time":60000}'`

### 调试日志与诊断 🐛

启用全面的故障排除日志记录：

```yaml
# 添加到 configuration.yaml
logger:
  default: warning
  logs:
    custom_components.openwrt_ubus: debug
    custom_components.openwrt_ubus.extended_ubus: debug
    custom_components.openwrt_ubus.shared_data_manager: debug
    homeassistant.components.device_tracker: debug
```

**日志分析技巧：**
- **连接问题**：查找 "Failed to connect" 或 "Timeout" 消息
- **认证问题**：搜索 "401" 或 "authentication failed" 错误
- **设备检测**：检查 "No devices found" 或解析错误
- **服务控制**：监控 "Service operation failed" 消息

### 性能监控 📊

使用内置指标监控集成性能：
- **API 响应时间**：检查日志中的慢 ubus 调用（>5秒）
- **更新间隔**：验证传感器在预期时间框架内更新
- **错误率**：监控重复发生的连接或解析错误
- **内存使用**：确保 Home Assistant 内存保持稳定

## 👨‍💻 开发与架构

### 项目结构 📁
```
custom_components/openwrt_ubus/
├── __init__.py              # 主集成设置和协调器管理
├── config_flow.py           # 用户配置流程和验证
├── const.py                 # 常量、默认值和配置架构
├── device_tracker.py        # 设备跟踪平台实现
├── sensor.py               # 传感器平台协调器和实体管理
├── switch.py               # 具有实时状态的服务控制开关
├── button.py               # 服务控制和设备踢出按钮协调
├── extended_ubus.py        # 增强的 ubus 客户端，支持批量 API 和 hostapd
├── shared_data_manager.py  # 集中数据管理和缓存优化
├── manifest.json           # 集成清单和依赖项
├── strings.json            # UI 字符串和用户界面文本
├── services.yaml           # 服务操作定义
├── Ubus/                   # 核心 ubus 通信库
│   ├── __init__.py
│   ├── const.py           # ubus 协议常量
│   └── interface.py       # 低级 ubus 接口实现
├── buttons/                # 按钮实体模块
│   ├── __init__.py
│   ├── service_button.py   # 服务控制按钮（启动/停止/重启/启用/禁用）
│   └── device_kick_button.py # 设备踢出功能与 hostapd 集成
├── sensors/                # 各个传感器平台模块
│   ├── __init__.py
│   ├── system_sensor.py    # 系统信息传感器（运行时间、内存、负载）
│   ├── qmodem_sensor.py    # QModem/LTE 传感器（信号、连接、数据）
│   ├── sta_sensor.py       # 无线站点传感器（每设备指标）
│   └── ap_sensor.py        # 接入点传感器（接口状态）
└── translations/           # 多语言支持的本地化文件
    ├── en.json            # 英文翻译
    └── zh.json            # 中文翻译
```

### 集成架构 🏗️

**数据流架构：**
1. **SharedDataUpdateCoordinator**：具有批量 API 优化的中央数据管理
2. **ExtendedUbus**：增强的 ubus 客户端，集成 hostapd 和错误处理
3. **平台模块**：专门的传感器/实体实现
4. **缓存层**：具有失效策略的智能缓存

**关键设计模式：**
- **协调器模式**：具有实体订阅的集中数据更新
- **工厂模式**：基于检测到的设备/服务的动态实体创建
- **观察者模式**：使用最少 API 调用的实时更新
- **策略模式**：可配置的无线/DHCP 检测方法

### 贡献指南 🤝

1. **🍴 Fork 仓库**：创建您自己的开发 fork
2. **🌿 创建功能分支**：使用描述性分支名称（`feature/device-kick-improvements`）
3. **✏️ 代码质量**：遵循 Home Assistant 开发指南
4. **🧪 彻底测试**：使用各种 OpenWrt 配置进行测试
5. **� 记录更改**：更新 README 和代码注释
6. **�📤 提交 Pull Request**：提供详细的更改描述

**开发设置：**
- 使用多个 OpenWrt 版本测试（21.02、22.03、snapshot）
- 验证与不同无线驱动程序的兼容性（ath9k、ath10k、mt76）
- 测试各种硬件平台（MIPS、ARM、x86）

## 📄 许可证

本项目根据 Mozilla Public License 2.0 (MPL-2.0) 许可 - 详情请参阅 LICENSE 文件。

## 🆘 支持与社区

- **🐛 GitHub Issues**：[报告错误或请求功能](https://github.com/fujr/homeassistant-openwrt-ubus/issues)
- **💬 Home Assistant 社区**：[在论坛讨论](https://community.home-assistant.io/)
- **📖 OpenWrt 文档**：[官方 OpenWrt Wiki](https://openwrt.org/docs/start)
- **🔧 ubus 参考**：[OpenWrt ubus 文档](https://openwrt.org/docs/techref/ubus)

## 🙏 致谢

- **🔧 OpenWrt 项目**：提供优秀的开源路由器固件和强大的 API
- **🏠 Home Assistant 社区**：提供集成开发资源、测试和反馈
- **👥 贡献者与测试者**：通过错误报告、功能请求和代码贡献帮助改进此集成的社区成员
- **📚 文档贡献者**：特别感谢帮助改进和完善此文档的贡献者

---

> **📝 欢迎文档贡献！**  
> 此 README 受益于社区的大量投入。如果您发现可以改进的地方、不清楚的说明或缺失的信息，请通过 issues 或 pull requests 贡献。您的经验和反馈有助于为每个人改进此集成！
