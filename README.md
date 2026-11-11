# GNOME Appearance

一键安装 GNOME 主题、图标和鼠标指针，包含资源校验、安装前备份和恢复功能。

默认外观：**Orchis-Dark + Tela-dark + Breeze（24）**，启用 Shell 主题和 GTK4/libadwaita 全局样式。

## 一键安装

在已登录 GNOME 的桌面终端中，以普通用户执行：

```sh
curl -fsSL https://raw.githubusercontent.com/XARKUR/gnome-appearance/main/install.sh | sh
```

需要 Python 3、curl 和已安装的 GNOME 桌面。脚本会下载项目，在 Debian/Ubuntu 或 Arch 上补齐缺少的生成工具，然后安装并应用外观。仅补齐依赖时会通过 `sudo` 请求管理员认证；不要给整个安装命令加 `sudo`。不自动升级系统、重启或注销。第一次安装 User Themes 后，请注销并重新登录。

也可以下载或克隆项目，查看脚本后运行 `./install.sh`。重复执行可更新外观；先前的有效备份会保留。

## 按需安装

以下以下载后的项目为例；在线命令也可以用 `| sh -s -- 参数` 传入相同选项。

```sh
./install.sh --dry-run                  # 只检查资源、预览组件和缺少的依赖
./install.sh --only icons               # 只安装图标
./install.sh --only cursor              # 只安装指针
./install.sh --only theme --no-shell    # GTK 主题，不设置 Shell
./install.sh --only theme --no-gtk4     # 本次不改 GTK4 样式链接
./install.sh --skip-deps                # 自行管理依赖
```

`--only` 支持 `theme,icons,cursor` 的逗号组合。`--no-gtk4` 不会撤销之前已有的 GTK4 链接；需要撤销时使用恢复命令。

GTK4/libadwaita 全局换肤会影响应用样式；如果某个应用显示异常，可以单独恢复 GTK4。壁纸、字体、文字缩放、快捷键、Dock 和其他扩展的设置不在本项目的修改范围内。

## 恢复

```sh
./install.sh restore                    # 恢复最近一次有效更改
./install.sh restore --only gtk4        # 只恢复 GTK4 文件
./install.sh restore --only icons       # 只恢复图标
```

无需保留下载目录，也可以在线恢复：

```sh
curl -fsSL https://raw.githubusercontent.com/XARKUR/gnome-appearance/main/install.sh | sh -s -- restore
```

记录保存在 `${XDG_STATE_HOME:-~/.local/state}/gnome-appearance/`。恢复会核对文件和设置，发现后续手动修改时先停止；确认要覆盖这些修改后可添加 `--force`。安装途中失败会尝试回退；依赖软件包不随外观恢复而卸载。

## 兼容范围

| 环境 | Shell 样式 | 验证情况 |
| --- | --- | --- |
| GNOME 43 | Orchis 上游样式 | 保留兼容路径及资源检查 |
| Debian 13 / GNOME 48 | Orchis 上游样式 | 已完成安装、图标解码和 GTK 样式解析检查 |
| Arch / GNOME 51 | 基于 51.1 的本项目移植 | Sass 编译通过，完整会话视觉验收待完成 |

随项目提供对应的官方 User Themes 扩展；已有兼容版本时优先复用。未适配的 Shell 版本会拒绝应用 Shell 样式，可用 `--no-shell` 安装其他部分。自动检查不能替代所有应用、缩放和布局的人工验收。

GNOME 51 样式使用固定的原生 51.1 控件规则，接入 Orchis 的色板、几何参数和弹窗样式，源码位于 `resources/shell-51/`；这不是 Orchis 官方的 GNOME 51 发布版本。

## 依赖与安装位置

一键入口自动补齐所选组件缺少的依赖：

| 发行版 | 常用生成依赖 |
| --- | --- |
| Debian / Ubuntu | `sassc libgtk-3-bin libglib2.0-bin gtk2-engines-murrine gnome-themes-extra` |
| Arch | `sassc gtk3 glib2 gtk-engine-murrine gnome-themes-extra` |

其他发行版请自行安装相应工具后使用 `--skip-deps`。系统已有依赖时不重新安装；Arch 不执行单独刷新包数据库或全系统升级。若软件包索引过期，请先按发行版方式维护软件源。

主题位于 `~/.themes/`，图标和指针位于 `${XDG_DATA_HOME:-~/.local/share}/icons/`。GTK4 链接位于 `${XDG_CONFIG_HOME:-~/.config}/gtk-4.0/`，原有文件先备份，`settings.ini` 保留。生成过程在临时 HOME 和资源副本中完成。

## 高级用法

`appearance.sh` 默认仅预览；它不自动安装依赖。

```sh
./appearance.sh
./appearance.sh check
./appearance.sh status
./appearance.sh plan --gnome-version 51.1
./appearance.sh build-shell --gnome-version 48
./appearance.sh build-shell --gnome-version 51.1
```

`build-shell` 仅生成项目内的构建产物，不应用外观。`--gnome-version` 仅用于预览、检查和构建；真实安装始终检测实际 Shell 版本。

编辑 `profile.json` 可修改组件、配色和尺寸。支持 `orchis`、`tela` 和标准主题目录 `directory` 三种适配器。使用其他资源时可传入 `--config 配置文件 --sources 资源目录`，配置中的 `source` 必须是该目录内的相对路径。

## 图标完整性处理

Tela 快照中的 1,471 个空 SVG 别名按照固定官方提交恢复；同时修正已声明但缺失的三倍缩放目录和一个错误别名目标。安装前后会检查链接依赖、空图像和图标缓存，防止只复制深色目录时丢失基础图标。恢复记录见 `resources/tela-link-recovery.json`。

## 开发检查与许可

```sh
sh -n install.sh
bash -n appearance.sh
python3 -m unittest discover -s tests -v
```

测试在临时目录和模拟设置中运行，不应用真实桌面外观。资源来源及 SHA256 见 `resources/manifest.json`；自动下载项目后仍会核对随包资源。

安装器原创代码采用 MIT 许可。Orchis、Tela、GNOME 和 Breeze 各自保留原许可、作者信息及对应源码，详见 [NOTICE.md](NOTICE.md)。Breeze 使用可核验的 KDE 5.27.5 / Debian 发行内容，具体版本、二进制来源和源码归档记录在 `resources/breeze.lock.json`。
