# PuddingPods 三路电量修复

给 [PuddingPods](https://github.com/lingbai-rong/PuddingPods)（`rongyi.puddingpods` 3.47，作者 融億 / 灵白）修的补丁仓库：
**左耳电量正常、右耳与充电盒恒显示 0**。作者在 3.45 更新日志里写过「耳機的電量問題，暫時沒有修復的方案」——现在能修了。

| | |
|---|---|
| 目标模块 | PuddingPods `rongyi.puddingpods` **3.47** |
| 适用耳机 | MOONDROP 水月雨 **PUDDING / MD-TWS-056**（Qualcomm GAIA v4，vendor `0x1D`） |
| 症状 | 左耳电量正常，**右耳 / 充电盒恒 0**；ANC、增益、指示灯的状态回读同样不对 |
| 改动量 | smali **一条指令**：`add-int/lit8 v2, v2, 0x4`；发送方向（TX）一字节未改 |
| 状态 | ✅ 真机验证：三路电量恢复正常 |
| 许可 | GPL-3.0（见文末「许可与免责声明」） |

上游沟通：[**issue #1**](https://github.com/lingbai-rong/PuddingPods/issues/1)（根因 + 一行修法）· [**PR #2**](https://github.com/lingbai-rong/PuddingPods/pull/2)（协议文档补充分帧长度语义）

---

## 1. 想直接装：三步

1. **取包**：本仓库 CI 的产物 —— `Actions → build-patched-apk → 最新一次成功运行 → Artifacts → PuddingPods-3.47-batteryfix-debug`（内含 `PuddingPods-3.47-batteryfix.apk`）。
   > CI 产物需要登录 GitHub 查看，且默认 90 天后过期。想自己编见 [§5 构建](#5-构建)，改一行指令也能手搓。
2. **先卸载原版**（必须）：改过签名的包 Android 不允许覆盖安装。
   `pm uninstall rongyi.puddingpods`，或 设置 → 应用 → PuddingPods → 卸载。
3. **装包 + 重新接 LSPosed**：安装后到 LSPosed 里重新启用模块并勾选作用域，然后重启作用域（或重启手机）：
   `com.android.bluetooth`、`com.xiaomi.bluetooth`、`com.milink.service`、`com.android.systemui`、`com.android.settings`

⚠️ 卸载会清掉模块自己的设置（伪装型号、超级岛、通知动作等）。**建议先截图备份**，装好后照着调。

回滚：原版 APK 在 `base/PuddingPods-3.47-stock.apk`（sha256 `ebe7c11a…`），直接装回去即可（同样要先卸载补丁版）。

---

## 2. 根因：分帧长度字段少了 4 字节

模块的 GAIA 分帧器把设备的长度字段当成了**整个帧体**的长度：

```
模块假设：body = [seq][vendor][feature][cmd][payload]   → consumed = 4 + len
设备实际：len 只数 payload                               → consumed = 8 + len
```

PUDDING 的长度字段**不含**那 4 字节头部：

```
→ FF 04 00 02 00 1D 1A 01 01 02                  查询电量（len=2，帧体=6）
← FF 04 00 06 00 1D 1B 01 01 64 02 64 03 5E      电量响应（len=6，帧体=10）
                 └ seq vendor feat cmd ┘└ 01 64 ┘└ 02 64 ┘└ 03 5E ┘
                                         左耳100% 右耳100% 充电盒94%
```

于是每条响应都被**截掉最后 4 字节**：解析器 `hm1;->a([B)` 的 `[id][level]` 循环在第一组之后就结束，
左耳排第一组能出来，右耳和充电盒取不到值而保持 0。
ANC（`0x41`）、增益（`0x1F`）、指示灯（`0x27`）的状态回读是同一个截断。

### 2.1 证据链

**设备协议** —— 出自第三方抓包逆向 [roxyyn0304/moondrop-gaia-protocol](https://github.com/roxyyn0304/moondrop-gaia-protocol)
（基于 MOONDROP Link v2.23.1c 的 5 次 btsnoop 抓包）：规律是 `帧体长度 = 长度字段 + 4`。

**模块代码**（反编译 3.47）—— 分帧器两条长度分支汇合在 `:goto_7e`：

```smali
:cond_73                        # 1 字节长度分支
    ...
    and-int/2addr v2, v3        # v2 = buf[3] & 0xFF   ← 设备长度字段
:goto_7e
    and-int/lit8 v0, v0, 0x1
    ...
    add-int v0, v6, v2          # consumed = 4 + len     ← 少算了 4
    add-int/2addr v0, v5
    ...
    new-array v3, v2, [B        # 切片长度 = len         ← 同样少算 4
```

**解析器**（逻辑本身正确，只是拿不到数据）—— 实际只收到 `00 1D 1B 01 01 64`（6 字节），循环在第二组前就因数组结束而退出：

```java
if (p[0]==0 && p[1]==0x1D && p[2]==0x1B && p[3]==1) {
    for (int i = 4; i + 1 < p.length; i += 2) {      // [id][level] 循环
        int id = p[i] & 255, level = p[i+1] & 255;
        if (id==1) left = level; else if (id==2) right = level; else if (id==3) case = level;
    }
}
```

### 2.2 离线复现（不需要耳机）

```console
$ python3 tools/simulate_fix.py
device frame: FF 04 00 06 00 1D 1B 01 01 64 02 64 03 5E  (len field = 0x06)

[before fix] frame sliced: 00 1D 1B 01 01 64
[before fix] consumed 10/14 bytes, leftover 02 64 03 5E
[before fix] parsed battery: {'left': 100}          ← 右耳/充电盒丢失，且残渣留在缓冲区

[after fix ] frame sliced: 00 1D 1B 01 01 64 02 64 03 5E
[after fix ] consumed 14/14 bytes, leftover -
[after fix ] parsed battery: {'left': 100, 'right': 100, 'case': 94}
```

顺带说明：修复前每次响应都会往缓冲区留 4 字节残渣，要靠丢弃字节重新同步；修复后帧边界严丝合缝。

---

## 3. 修复内容

`tools/patch_smali.py` 在两条长度分支的汇合点插入一条指令：

```diff
 :goto_7e
+    # [puddingpods-battery-fix] length field excludes the 4-byte [seq][vendor][feature][cmd] prefix
+    add-int/lit8 v2, v2, 0x4
     and-int/lit8 v0, v0, 0x1
```

**为什么一条就够**：1 字节与 2 字节长度两条分支都汇合到 `:goto_7e`，之后 `v2` 是切片长度、拷贝循环上界、
`consumed` 的唯一来源，加 4 一次全对。

**为什么不会打歪**：脚本不写死类名（酷安 3.47 是 `Llc1;`、GitHub release 3.47 是 `Lmc1;`），
而是按方法签名 `.method public b([B)Ljava/util/ArrayList;` + 代码指纹（`and-int/2addr v2, v3`、`or-int/2addr v2, v4`）
定位**唯一**的类；找不到、找到多个、锚点不唯一，一律直接失败退出，不会产出「补丁没打上」的静默错误包。脚本幂等，重复运行无副作用。

**TX 不动**：模块自建的查询 / 设置帧一字节未改，避免影响现已可用的降噪 / 增益下发。

---

## 4. 影响面与回归自测

分帧器是**所有** GAIA 响应的入口，修复前它们都被同样截断：

| 项目 | 修复前 | 修复后 |
|---|---|---|
| 左耳电量 | 正常 | 正常 |
| 右耳 / 充电盒电量 | 恒 0 | 正确 |
| 耳机上按一下切降噪，模块 UI 是否跟随 | 不跟随（响应被截断） | 跟随 |
| 增益 / 指示灯状态回读 | 被截断 | 正常 |

自测建议：连上耳机看超级岛 / 通知里的三路电量；在耳机上切一次降噪，看模块是否同步。

---

## 5. 构建

### 5.1 GitHub Actions（推荐）

push 到 `main`（改动 `base/**`、`tools/**`、`keystore/**`、workflow 本身时触发）或手动 `workflow_dispatch`，
产物名 `PuddingPods-3.47-batteryfix-debug`。

流程：`baksmali` 反汇编 → 打补丁 → `smali -a 35` 回汇编（dex 039，与原 dex 一致）→
在**原 APK 内只替换 `classes.dex`**（其余 78 个条目原样保留）→ `zipalign` → 用仓库内固定密钥签名。

CI 先校验基座哈希（`BASE_SHA256`）再动手，基座对不上就中止。

### 5.2 本地

```bash
# 依赖: jdk17+, python3, unzip/zip, Android build-tools (zipalign + apksigner)
curl -LO https://bitbucket.org/JesusFreke/smali/downloads/baksmali-2.5.2.jar
curl -LO https://bitbucket.org/JesusFreke/smali/downloads/smali-2.5.2.jar
BASE_APK=base/PuddingPods-3.47-stock.apk \
ZIPALIGN=/path/to/zipalign APKSIGNER=/path/to/apksigner \
bash tools/build.sh          # → out/PuddingPods-3.47-batteryfix.apk
```

换基座（例如用 GitHub release 那份 3.47）只要改 `BASE_APK` 指向对应 APK 即可，补丁脚本会自动定位该类：

```bash
BASE_APK=/path/to/official-3.47.apk bash tools/build.sh
```

---

## 6. 已知限制

- **两个 3.47 不是同一个包**：本仓库钉的基座是**酷安那份**（sha256 `ebe7c11a…`，分帧器类 `Llc1;`）；
  GitHub release 的 3.47（`dd389789…`，类 `Lmc1;`）字节数相同但是另一次构建，`classes.dex` / `AndroidManifest.xml` /
  `baseline.prof` 都不同。补丁脚本两份都能自动定位并打上（方法体逐行一致），但**真机验证用的是酷安基座那份**。
- 上游若发布新版本，先跑 `tools/patch_smali.py` 试锚点（不匹配会直接报错），再同步 `BASE_APK` 与 `BASE_SHA256`。
- 分帧器会把 `FF 04` 后的第 3 个字节当 GAIA flags 用（bit0 = 尾字节、bit1 = 2 字节长度）。实测该字节恒为 `0x00`
  （模块自己发 TX 时也硬编码 0），所以不影响；若某天固件在这里放非 0 值，分帧会按 flags 语义走偏。本次**刻意不动**这段逻辑，保持最小改动面。
- smali 回汇编的 dex 不保证逐字节一致（体积也偏大：3.2MB → 6.3MB，功能无影响）。校验请比对反汇编结果或跑真机，别拿哈希比。
- 这是**对闭源模块的二进制补丁**，不是上游源码修复。作者若采纳，正确改法就是把分帧长度按「payload 长度 + 4」处理——
  上游 issue [#1](https://github.com/lingbai-rong/PuddingPods/issues/1) 里写了这一点。
- 签名密钥 `keystore/puddingfix.jks`（别名 / 口令 `puddingfix` / `android`）是**公开的测试密钥**，只用于本仓库产物，不要拿去签别的东西。

---

## 7. 文件

```
tools/patch_smali.py     # 打补丁（签名+指纹定位、幂等、失败即退出）
tools/simulate_fix.py    # 用抓包数据离线复现 bug 与修复
tools/build.sh           # 完整构建脚本（CI 与本地共用）
keystore/puddingfix.jks  # 固定测试签名密钥
base/…-stock.apk         # 未修改的基座 APK（酷安 3.47，sha256 ebe7c11a…）
.github/workflows/build.yml
LICENSE                  # GPL-3.0
```

---

## 8. 许可与免责声明

- **本仓库自己的内容**（补丁脚本、构建脚本、模拟脚本、文档）按 **GPL-3.0** 发布，见 [LICENSE](LICENSE)。
  选 GPL-3.0 是为了与上游一致：上游 PuddingPods 在 README 里声明 GPL-3.0，其基座 HyperPods 也是 GPL-3.0
  （OppoPods 无许可声明、Miuix 为 Apache-2.0，本仓库均未使用其代码）。
- **基座 APK 的版权归原作者 融億**（[酷安主页](http://www.coolapk.com/u/30640322)）所有。
  它在这里只作为补丁构建的输入保留，本仓库不对它主张任何权利。
  注意：上游声明 GPL-3.0，但**从未公开 `app/` 源码**，因此任何人（包括上游自己）再分发这个 APK 时都无法满足
  GPL-3.0 第 6 条的「提供对应完整源码」义务；本仓库因此**不挂 Release 二进制**，只保留补丁与构建脚本，产物走 CI artifact。
- **非官方**：本仓库与 MOONDROP / 水月雨、小米 / HyperOS、PuddingPods 作者均无隶属或合作关系，
  也不代表他们认可本项目。相关商标归各自权利人所有，此处仅作兼容性说明（指代性使用）。
- **自担风险**：这是对第三方闭源模块的二进制补丁，仅供个人在自己的设备上做互操作性修复使用。
  刷模块有变砖 / 丢配置 / 账号策略风险，请先备份原版 APK 与模块设置。作者若要求下架，会立即处理。
