# ha-evecca

`ha-evecca` 是一个非官方的 Home Assistant 自定义集成，用于控制易慧家（EVECCA）智能门窗控制器。

## 已测试支持的设备

- 易慧家智能门窗控制器 3C
- 易慧家内倒窗 / 平开窗执行器
- 易慧家内倒锁 / 平开锁
- 已验证 10 组控制器、窗户和锁

## 功能

支持在 Home Assistant 中控制易慧家窗户。

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
8. 选择密码登录、短信验证码登录，或按页面提示导入 App 会话。
9. 选择要接入的家庭。

也可以直接打开 HACS 仓库链接：

[在 HACS 中打开 ha-evecca](https://my.home-assistant.io/redirect/hacs_repository/?owner=jackjinke&repository=ha-evecca&category=integration)

## 注意事项

- 这是非官方集成，需要连接易慧家云服务。
- 易慧家云端接口变化可能导致登录或控制失效。
- 集成每约 6 小时自动续期登录会话；密码、短信验证码和 App 会话登录均支持，无需保存密码。独立定时检查不受 MQTT 推送影响，临时网络故障会在下一次检查（约 5 分钟后）重试。
- 状态查询或控制请求遇到认证拒绝时，集成会先续期并重试一次；超时或普通接口错误不会重发控制命令。续期后的令牌和 MQTT 凭据会自动保存，MQTT 凭据变化时自动重连。
- 云端拒绝续期，或续期后仍拒绝认证时，仍需通过密码、短信验证码或新的 App 会话重新登录。云端会话有效期及续期是否延长有效期尚未确认，自动续期不能保证避免所有重新认证。
- 命令成功发送时不弹出通知；发送失败和设备上报的事件仍会通知。
- 其他易慧家设备类型暂未测试。

## 许可证

[MIT](LICENSE)
