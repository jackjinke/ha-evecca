# ha-evecca

`ha-evecca` 是一个非官方的 Home Assistant 自定义集成，用于通过 EVECCA 云服务控制 EVECCA 智能门窗控制器。

集成会把云端窗户设备创建为 Home Assistant 的 `cover` 实体，并使用 MQTT 接收实时状态推送，同时用低频 HTTPS 请求校正状态。

## 已测试支持的设备

- EVECCA 智能门窗控制器 3C
- 云端型号：`evecca.win.218`
- 固件版本：`2009`
- 已在真实 EVECCA 云端验证 10 台设备的登录、设备枚举、位置读取和 MQTT 在线状态推送

已实现控制功能：

- 打开
- 关闭
- 停止
- 设置开合百分比
- 悬开 / 内倒模式

其他 EVECCA 设备类型暂未测试。

## 工作原理

- HTTPS：登录、刷新会话、获取设备列表、发送控制命令
- MQTT over TLS：接收设备在线状态和位置变化推送
- Home Assistant：以窗户类型的 `cover` 实体展示和控制设备

该集成依赖 EVECCA 云服务，不是 RS485 或纯局域网本地控制。

## 通过 HACS 安装

1. 打开 Home Assistant 中的 **HACS**。
2. 进入 **Integrations**，点击右上角菜单，选择 **Custom repositories**。
3. 添加仓库：

   ```text
   https://github.com/jackjinke/ha-evecca
   ```

4. 类型选择 **Integration**。
5. 在 HACS 中搜索并下载 **EVECCA**。
6. 重启 Home Assistant。
7. 进入 **设置 → 设备与服务 → 添加集成**，搜索 `EVECCA`。
8. 使用 EVECCA 账号密码登录，或选择短信验证码登录。
9. 选择要接入的家庭，窗户会以 `cover` 实体出现。

也可以直接打开 HACS 仓库链接：

[在 HACS 中打开 ha-evecca](https://my.home-assistant.io/redirect/hacs_repository/?owner=jackjinke&repository=ha-evecca&category=integration)

## 注意事项

- 这是非官方集成；EVECCA 云端接口变化可能导致登录或控制失效。
- 账号 token 失效时，集成会要求重新认证。
- 开合百分比采用 Home Assistant 约定：`0` 表示关闭，`100` 表示完全打开。
- 云端里重复的设备名称会自动附加设备编号后缀，便于区分。

## 许可证

[MIT](LICENSE)
