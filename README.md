# PuddingPods 三路电量修复：右耳 / 充电盒恒 0

针对 [PuddingPods](https://github.com/Xposed-Modules-Repo/rongyi.puddingpods)（rongyi.puddingpods 3.47，作者 融億）在 MOONDROP 水月雨 PUDDING / MD-TWS-056 上
**左耳电量正常、右耳与充电盒恒显示 0** 的问题：定位到根因并给出最小修复（一条指令），附带可复现的构建流程。

> 作者在 3.45 的更新日志里写过「耳機的電量問題，暫時沒有修復的方案」——这个 bug 现在可以修了。

---

## 1. 结论

模块的 GAIA 分帧器 `Llc1;->b([B)` 把设备长度字段当成了**整个帧体**的长度：

```
模块假设： body = [seq][vendor][feature][cmd][payload]   → consumed = 4 + len
设备实际： len 只数 payload                               → consumed = 8 + len
```

于是每条响应都被**截掉最后 4 字节**。电量响应 `[id][level]` 三元组里，左耳排第一组能解析出来，右耳和充电盒直接被切掉 → 恒 0。

修复：在 `Llc1;->b([B)` 两条长度分支的汇合点把长度 `+4`。一条指令，同时修正切片长度、拷贝循环、已消费字节数。

---

## 2. 证据链

### 2.1 设备协议（第三方 btsnoop 抓包逆向，已交叉验证）

出自 [roxyyn0304/moondrop-gaia-protocol](https://github.com/roxyyn0304/moondrop-gaia-protocol)（基于 MOONDROP Link v2.23.1c 的 5 次 btsnoop 抓包）：

```
→ FF 04 00 02 00 1D 1A 01 01 02                  查询电量（len=2, 帧体=6）
← FF 04 00 06 00 1D 1B 01 01 64 02 64 03 5E      电量响应（len=6, 帧体=10）
                   └ seq vendor feat cmd ┘└ 01 64 ┘└ 02 64 ┘└ 03 5E ┘
                                           左耳100%  右耳100%  充电盒94%
```

规律：`帧体长度 = 长度字段 + 4`，多出来的 4 字节是 `[seq][vendor][feature][cmd]`。

### 2.2 模块代码（反编译你手机上的 3.47）

分帧器 `Llc1;->b([B)`：

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

解析器 `hm1;->a([B)`（逻辑本身正确，只是拿不到数据）：

```java
if (p[0]==0 && p[1]==0x1D && p[2]==0x1B && p[3]==1) {
    for (int i = 4; i + 1 < p.length; i += 2) {      // [id][level] 循环
        int id = p[i] & 255, level = p[i+1] & 255;
        if (id==1) left = level; else if (id==2) right = level; else if (id==3) case = level;
    }
}
```

`hm1;->a()` 收到的实际是 `00 1D 1B 01 01 64`（6 字节），循环在第二组之前就因数组结束而退出 → 只有左耳。

### 2.3 离线复现

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

顺带说明：修复前每次响应都会往缓冲区留 4 字节残渣，需要靠丢弃字节重新同步；修复后帧边界严丝合缝。

---

## 3. 修复内容

`tools/patch_smali.py` 在 `Llc1;->b([B)` 里插入一条指令：

```diff
 :goto_7e
+    # [puddingpods-battery-fix] length field excludes the 4-byte [seq][vendor][feature][cmd] prefix
+    add-int/lit8 v2, v2, 0x4
     and-int/lit8 v0, v0, 0x1
```

为什么一条够：1 字节长度与 2 字节长度两条分支都汇合到 `:goto_7e`，之后 `v2` 是切片长度、拷贝循环上界、`consumed` 的唯一来源，全部一起被纠正。
脚本带指纹校验（找不到唯一汇合点或代码形状不符就直接失败），不会产出「补丁没打上」的静默错误包。

**发送方向（TX）不动**：模块自建的查询/设置帧一字节未改，避免影响现已正常工作的降噪/增益下发。

---

## 4. 构建

### GitHub Actions（推荐）

push 到 `main` 或手动 `workflow_dispatch` 即出包，产物名 `PuddingPods-3.47-batteryfix-debug`。

流程：`baksmali` 反汇编 → 打补丁 → `smali -a 35` 回汇编（dex 039，与原 dex 一致）→ 在**原 APK 内只替换 `classes.dex`**（其余 78 个条目原样保留）→ `zipalign` → 用仓库内固定密钥签名。

基座 APK 钉在 `base/PuddingPods-3.47-stock.apk`（sha256 `ebe7c11a…`），CI 会先校验哈希再动手。

### 本地

```bash
# 需要: jdk17, python3, unzip/zip, Android build-tools (zipalign + apksigner)
curl -LO https://bitbucket.org/JesusFreke/smali/downloads/baksmali-2.5.2.jar
curl -LO https://bitbucket.org/JesusFreke/smali/downloads/smali-2.5.2.jar
BASE_APK=base/PuddingPods-3.47-stock.apk \
ZIPALIGN=/path/to/zipalign APKSIGNER=/path/to/apksigner \
bash tools/build.sh
```

---

## 5. 安装注意（重要）

- 修改后的包**用的是新的签名**，Android 不允许覆盖安装 → **必须先卸载原版**：
  `pm uninstall rongyi.puddingpods`（或 设置 → 应用 → PuddingPods → 卸载）
- 卸载会清掉模块自己的配置（伪装型号、超级岛设置、通知动作等），装好后需要**重新在 LSPosed 里勾选作用域**并重启作用域：
  `com.android.bluetooth`、`com.xiaomi.bluetooth`、`com.milink.service`、`com.android.systemui`、`com.android.settings`
- 原版 APK 备份在 `base/PuddingPods-3.47-stock.apk`，要回滚直接装回去即可
- 建议先在 App 里把设置项截个图，重装后照着调

---

## 6. 影响面 / 回归自测

这个分帧器是**所有** GAIA 响应的入口（电量 `0x1B`、降噪 `0x41`、增益 `0x1F`、指示灯 `0x27`），修复前它们都被同样截断：

| 项目 | 修复前 | 修复后 |
|---|---|---|
| 左耳电量 | 正常 | 正常 |
| 右耳 / 充电盒电量 | 恒 0 | 正确 |
| 耳机上按一下切降噪，模块 UI 跟随 | 不会跟随（响应被截断） | 应跟随 |
| 增益 / 指示灯状态回读 | 被截断 | 正常 |

自测建议：连上耳机后看超级岛/通知里的三路电量；在耳机上切一次降噪看模块是否同步。

---

## 7. 已知限制

- 基座是你手机上实际安装的 3.47 构建（sha256 `ebe7c11a…`）。GitHub release 里那份 3.47（`dd389789…`）是**另一个构建**，dex/manifest/baseline.prof 都不同，不要混用。
- 上游若发布新版本，需要重新跑 `tools/patch_smali.py`（锚点不匹配会直接报错），并同步 `BASE_APK` 哈希。
- 分帧器把 `FF 04` 后面的第 3 个字节当 GAIA flags 用（bit0 = 尾字节、bit1 = 2 字节长度）。实测这个字节恒为 `0x00`（模块自己发 TX 时也硬编码 0），所以不影响；但如果哪天的固件在这里放非 0 值（例如真的当序列号递增），分帧会按 flags 语义走偏。本次**刻意不动**这段逻辑，保持最小改动面。
- smali 回汇编的 dex 每次字节不保证完全一致（段顺序不稳定，体积也偏大：3.2MB → 6.3MB，功能无影响）。所以校验要比对反汇编结果或跑真机，别拿哈希比。
- 这是对闭源模块的二进制补丁，不是上游源码修复；作者若采纳，正确的改法就是把分帧长度按「payload 长度 + 4」处理。

---

## 8. 给作者的 issue 草稿

> **标题**：电量响应被分帧器截断 4 字节，导致右耳/充电盒恒 0
>
> `Llc1;->b([B)` 里 `consumed = v6 + len + trailing`、`new-array v3, v2, [B`，把 `FF 04 00 <len>` 的 `<len>` 当成了整个帧体长度。
> 但设备（GAIA v4，vendor 0x1D）的 `<len>` 只数 payload，`[seq][vendor][feature][cmd]` 这 4 字节不计入：
>
> `← FF 04 00 06 | 00 1D 1B 01 | 01 64 02 64 03 5E` → 左耳100 / 右耳100 / 盒94
>
> 结果 `hm1;->a([B)` 只收到 `00 1D 1B 01 01 64`，`[id][level]` 循环在第一组后就结束：左耳正常，右耳和充电盒取不到值而保持 0。同一个截断也影响 ANC(0x41)、增益(0x1F)、指示灯(0x27) 的状态回读。
>
> 最小修复：在 `:goto_7e`（1 字节 / 2 字节长度两条分支的汇合点）后加 `add-int/lit8 v2, v2, 0x4` 即可；TX 方向无需改动。

---

## 9. 文件

```
tools/patch_smali.py     # 打补丁（幂等 + 指纹校验）
tools/simulate_fix.py    # 用抓包数据离线复现 bug 与修复
tools/build.sh           # 完整构建脚本（CI 与本地共用）
keystore/puddingfix.jks  # 固定签名密钥（口令 android / 别名 puddingfix，仅测试用）
base/…-stock.apk         # 未修改的基座 APK
.github/workflows/build.yml
```

基座 APK 版权归原作者 融億（[酷安](http://www.coolapk.com/u/30640322)）所有，此处仅作为补丁构建输入保留；本仓库只包含补丁与构建脚本。
