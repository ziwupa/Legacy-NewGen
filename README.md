<a href="https://www.codacy.com/gh/ziwupa/Legacy-NewGen/dashboard?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=ziwupa/Legacy-NewGen&amp;utm_campaign=Badge_Grade"><img src="https://app.codacy.com/project/badge/Grade/97e3ea868f9344a5aa6e4d874f83db14"/></a>
<a href="#"><img src="https://img.shields.io/github/languages/code-size/ziwupa/Legacy-NewGen"/></a>
<a href="#"><img src="https://img.shields.io/github/issues-raw/ziwupa/Legacy-NewGen"/></a>
<a href="#"><img src="https://img.shields.io/github/license/ziwupa/Legacy-NewGen"/></a>
<a href="#"><img src="https://img.shields.io/github/commit-activity/m/ziwupa/Legacy-NewGen"/></a><br>
<a href="#"><img src="https://img.shields.io/github/forks/ziwupa/Legacy-NewGen?style=flat"/></a>
<a href="#"><img src="https://img.shields.io/github/stars/ziwupa/Legacy-NewGen"/></a>&nbsp;<a href="https://github.com/psf/black"><img src="https://img.shields.io/badge/code%20style-black-000000.svg" alt="Code style: black"></a><br>

# Legacy NewGen

Maintained by [@ziwupa](https://t.me/ziwupa).

### Disclaimer

> If you are a paranoid person, you should not use this userbot. This userbot is not a virus, but it can be used for malicious purposes. You are responsible for all actions taken by your account.

<hr>
<h2><img src="https://img.icons8.com/?size=100&id=Jd0d5Iz2TZIb&format=png&color=000000" height="54" align="center" style="margin-right: 7px;">Installation</h2>

> [!NOTE]
> **VPS/VDS users:** add `--root` if you run as root (to avoid entering `force_insecure`).

### VPS/VDS

<b>Ubuntu / Debian</b>

```bash
sudo apt update && sudo apt install git python3 python3-venv -y && \
git clone https://github.com/ziwupa/Legacy-NewGen && \
cd Legacy-NewGen && \
python3 -m venv .venv && \
source .venv/bin/activate && \
pip install -r requirements.txt && \
python3 -m legacy
```

<b>Fedora</b>

```bash
sudo dnf update -y && sudo dnf install git python3 -y && \
git clone https://github.com/ziwupa/Legacy-NewGen && \
cd Legacy-NewGen && \
python3 -m venv .venv && \
source .venv/bin/activate && \
python3 -m pip install -r requirements.txt && \
python3 -m legacy
```

<b>Arch Linux</b>

```bash
sudo pacman -Syu --noconfirm && sudo pacman -S git python --noconfirm --needed && \
git clone https://github.com/ziwupa/Legacy-NewGen && \
cd Legacy-NewGen && \
python3 -m venv .venv && \
source .venv/bin/activate && \
python3 -m pip install -r requirements.txt && \
python3 -m legacy
```

<i>On a VPS/VDS add <code>--proxy-pass</code> at the end of the command to open an SSH tunnel to the web interface, or <code>--no-web</code> to finish the setup in console.</i><br>

### Other

<b>WSL (Windows)</b>

> [!WARNING]
> Can be unstable!

1. Install WSL. Open PowerShell as administrator and run:

```powershell
wsl --install -d Ubuntu-22.04
```

> [!IMPORTANT]
> Requires Windows 10 build 2004+ or Windows 11 of any version, on a machine with virtualization support. For earlier systems see [the manual installation guide](https://learn.microsoft.com/windows/wsl/install-manual).

2. Restart the PC and start **Ubuntu 22.04.x**.
3. Bootstrap pip (paste with right mouse button):

```bash
curl -Ss https://bootstrap.pypa.io/get-pip.py | python3
```

> [!NOTE]
> If yellow warnings appear, run `export PATH="$HOME/.local/bin:$PATH"` — replacing the path with the one mentioned in the message.

4. Install:

```bash
clear && git clone https://github.com/ziwupa/Legacy-NewGen && cd Legacy-NewGen && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python3 -m legacy
```

<b>Phone (UserLAnd)</b>

1. Install [UserLAnd](https://play.google.com/store/apps/details?id=tech.ula).
2. Open it and choose **Ubuntu → Minimal → Terminal**.
3. Wait for the distribution to install — you can pour some tea.
4. When the terminal opens, run:

```bash
sudo apt update && sudo apt upgrade -y && sudo apt install python3 python3-venv git python3-pip -y && \
git clone https://github.com/ziwupa/Legacy-NewGen && cd Legacy-NewGen && \
python3 -m venv .venv && source .venv/bin/activate && \
pip install -r requirements.txt && python3 -m legacy
```

5. At the end of the installation a link will appear — follow it and log into your account.

Voila! Legacy NewGen is installed on UserLAnd.

<b>🐬 Docker:</b><br>

```bash
git clone https://github.com/ziwupa/Legacy-NewGen && cd Legacy-NewGen && sudo docker build . -t ziwupa/legacy-newgen:latest
```

```bash
sudo docker run --restart=unless-stopped --name <container name> -p <port>:8080 --detach -it ziwupa/legacy-newgen:latest
```

<b>🚂 Deploy on <a href="https://railway.com/template/47hYUn?referralCode=PvevLV">Railway</a></b>

<i>Be careful! Any userbots are officially banned on Railway, if your account is banned, neither the creator nor other people are to blame for this</i>

<hr>
<h2><img src="https://img.icons8.com/?size=100&id=PClBimo4GQGJ&format=png&color=000000" height="54" align="center" style="margin-right: 7px;"> Changes</h2>

<ul>
    <li><a href="https://t.me/ziwupa">@ziwupa</a> for maintaining Legacy NewGen</li>
 <li>🆕 <b>Latest Telegram layer</b> with reactions, video stickers and other stuff</li>
 <li>🔓 <b>Security</b> improvements, including <b>native entity caching</b> and <b>targeted security rules</b></li>
 <li>🎨 <b>UI/UX</b> improvements</li>
 <li>📼 Improved and new <b>core modules</b></li>
 <li>▶️ <b>Inline forms, galleries and lists</b></li>
</ul>

<hr>
<h2 border="none"><img src="https://img.icons8.com/?size=100&id=5cJddikxEAhI&format=png&color=000000" height="54" align="center" style="margin-right: 7px;"> Requirements</h2>
<ul>
 <li>🐍 Python 3.10+</li>
 <li>🔑 API_ID and HASH from <a href="https://my.telegram.org/apps" color="#2594cb">Telegram</a></li>
</ul>

<hr>
<h2 border="none"><img src="https://img.icons8.com/?size=100&id=rLMbY01ZXrPE&format=png&color=000000" height="54" align="center" style="margin-right: 7px;"> Documentation</h2>

### 🔑 How to get API_ID and API_HASH

Create an application at <a href="https://my.telegram.org/apps">my.telegram.org/apps</a>.

### ✨ Key features

| Feature | Description |
| --- | --- |
| 🆕 Latest Telegram layer | Layer 228 — forums, Communities and the newest Telegram features |
| 🔒 Enhanced security | Native entity caching, targeted security rules, phone masking and scam-module protection |
| 🎨 UI/UX improvements | Modern interface, expandable help, custom emoji for non-premium accounts |
| 📦 Core modules | Improved and new core functionality |
| ⏱ Rapid bug fixes | Quick resolution of reported issues |
| ▶️ Inline elements | Forms, galleries and lists support |
| 🔒 Automatic backups | Database backuper with restore |

<hr>
<h2 border="none"><img src="https://img.icons8.com/?size=100&id=wuPAd75eU6lM&format=png&color=000000" height="54" align="center" style="margin-right: 7px;"> <a href="https://t.me/LegacyNewgensSupport">Support</a></h2>

<hr>
<h2 border="none"><img src="https://img.icons8.com/?size=100&id=YCbKhwUNH1pc&format=png&color=000000" height="54" align="center" style="margin-right: 7px;"> Features</h2>
<table>
 <tr>
  <td>
   ⌨️<b> Forms - bored of writing? Use buttons!</b>
  </td>
  <td>
   📷<b> Galleries - scroll your favorite photos in Telegram</b>
  </td>
 </tr>
 <tr>
  <td>
   <img src="https://i.postimg.cc/T3VSMbvQ/legacy-inline-form.gif">
  </td>
  <td>
   <img src="https://i.postimg.cc/1XDTmVN9/legacy-inline.gif">
  </td>
 </tr>
</table>
<table>
 <tr>
  <td>
   ➡️<b> Inline - share userbot with your friends</b>
  </td>
  <td>
   🤖<b> Bot interactions - "No PM"? No problem. Feedback bot at your service</b>
  </td>
 </tr>
 <tr>
  <td>
   <img src="https://i.postimg.cc/nzGcXrm1/legacy-inline-cmds.gif">
  </td>
  <td>
   <img src="https://i.postimg.cc/HsXHnVC8/legacy-feedback.gif">
  </td>
 </tr>
</table>
<table>
 <tr>
  <td>
   ⚠️<b> InlineLogs - traceback directly in message, caused error</b>
  </td>
  <td>
   ✏️<b> Grep - execute command and get only required lines</b>
  </td>
 </tr>
 <tr>
  <td>
   <img src="https://i.postimg.cc/FHHPqBGF/legacy-inline-logs.gif">
  </td>
  <td>
   <img src="https://i.postimg.cc/FzbcshFt/legacy-grep.gif">
  </td>
 </tr>
</table>

<b>👨‍👦 NoNick, NoNickUser, NoNickCmd, NoNickChat - use another account for userbot</b>
<img src="https://i.postimg.cc/wvX3DFCL/legacy-nonick.gif">

<hr>

### Warning

> This project is provided as-is. Developer doesn't take ANY responsibility over any problems, caused by userbot. By installing Legacy NewGen you take all risks on you. This is but not limited to account bans, deleted (by Telegram algorithms) messages, SCAM-modules, leaked sessions (due to SCAM-modules). It is **highly** recommended to enable the API Flood protection (.api_fw_protection) and not to install many modules at once. By using Legacy NewGen you give your consent to any actions made by your account in background in purposes of automatization. Please, consider reading <https://core.telegram.org/api/terms> for more information.

<hr>
<h2><img src="https://img.icons8.com/?size=100&id=haPxINLo0tRS&format=png&color=000000" height="54" align="center" style="margin-right: 7px;">Special thanks to:</h2>

<ul>
    <li><a href="https://gitlab.com/hackintosh5">Hackintosh5</a> for FTG, which is the base of Hikka</li>
    <li><a href="https://github.com/beveiled">Hikariatama</a> for Hikka, which is the base of project</li>
    <li><a href="https://t.me/GunyaKshin">Codwiz</a> for Ukrainian translation pack</li>
    <li><a href="https://t.me/Admt_450">ɴᴇᴛ『s』ᴛᴀʟᴋ『2』『4』</a> for testing, finding bugs and Ukrainian translation</li>
    <li><a href="https://t.me/lonami">Lonami</a> for Telethon, which is the base of Legacy-TL-New</li>
</ul>
