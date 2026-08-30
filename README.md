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
8. 使用易慧家账号密码登录，或选择短信验证码登录。
9. 选择要接入的家庭。

也可以直接打开 HACS 仓库链接：

[在 HACS 中打开 ha-evecca](https://my.home-assistant.io/redirect/hacs_repository/?owner=jackjinke&repository=ha-evecca&category=integration)

## 注意事项

- 这是非官方集成，需要连接易慧家云服务。
- 易慧家云端接口变化可能导致登录或控制失效。
- 账号登录失效时，集成会要求重新认证。
- 其他易慧家设备类型暂未测试。

## 许可证

[MIT](LICENSE)
