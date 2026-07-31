# Legacy newgen Changelog
## Legacy newgen 1.0.0

- Revived from the Legacy dev branch and maintained by @ziwupa
- Added Legacy-TL-NewGen 2.1.0.post1 with Telegram layer 228
- Added current Telegram TL types, bot MTProto primitives and payment methods
- Updated forum topic requests for the current Telegram API namespace
- Ported full MTProto inline stack from Heroku 2.1:
  - Replaced aiogram Bot/Dispatcher with second Telethon client via bot token
  - Added TelethonBot adapter preserving Bot-API-style send/edit/delete methods
  - Added support for all Bot API update types through raw TL events
  - Added persistent bot sessions with stale-session cleanup
  - Added ping-bot startup verification and FloodWait handling
  - Migrated form, gallery, list, query_gallery, bot_pm to Telethon builders
  - Replaced Pydantic models with pure Python composition
  - Updated InlineCall/BotInlineCall/BotInlineMessage types
  - Kept web auth token flow and invoice support
  - Added backward-compatible aiogram_watcher → bot_watcher bridge
- Fixed session shutdown during login
- Fixed web login requests without a 2FA password
- Added revoked session handling
- Moved user sessions into a dedicated directory with automatic migration
- Updated startup branding and project metadata
- Fixed inline list and gallery close buttons
- Added safe inline message deletion with callback and stored-unit fallbacks
- Raised minimum Python version to 3.10
- Fixed CreateForumTopicRequest parameter channel→peer for layer 228
- Fixed message.edit parameter media→file for new TL client
- Removed LimokaLegacy module (broken)
- Fixed inline list and gallery close buttons
- Added safe inline message deletion with callback and stored-unit fallbacks

## 🌙 Legacy: Continuum
# 💸 Financed by: @xdesai @ebed_tg - paid with souls, psyche, time and body (telethon fucked us in all holes)

- Switched to Telethon (a.k.a legacy-tl)
- Uptime cheating is allowed again for everyone
- Rollback added
- CPU usage fixed
- Removed some platforms
- Known bugs have been fixed
- Close button fixed
- Legacy code has been removed
- SECURITY GROUPS FINNALY FIXED (sgroups/tsec)
- Minor redesign
- German language pack removed
- Removed sandbox mode
- Bugged configs fixed
- All references to Unit Heta has been removed
- No more shit in logs
- New log levels added
- Fixed work in Docker
- Backup logic updated
- Forbid Constructors work has been restored
- Added authorization via inline bot
- Improved Ukrainian translation
- Added module search via Limoka

## 🌙 Legacy: Catalyst

- Improved UA translation
- Improved inline authorization
- Fixed LegacyHelp module
- Back button added in some places
- Blockquotes added in aliasescmd
- Sharkhost platform added
- Reworked rollbackcmd
- Added new keyword {label} to LegacyInfo
- Added the ability to specify a token for an inline bot via arguments and 'ch_bot_token' cmd
- utils.answer and utils.answer_file methods have been updated
- New CLI banner
- Added configuration for eval
- Methods have been refactored
- Termux platform removed
- Improved backward compatibility with Hikka
- Fixed terminating the process 
- Added custom prefixes for owners
- Removed debugger
- Fixed SudoMessageEditor in TerminalMod
- Added HikkaHost EULA
- Added multi-dlm/ulm
- Fixed configs
- Updated LegacyInfoMod
- Updatel Legacy-TL-New
- Dispatcher refactoring

## 🌙 Legacy: ReGenesis

- Added banner for ping
- Added new configs
- Improved strings
- Fixed grep
- Fixed loadmod output
- Updated Limoka (v1.3.0)
- Removed SharkHost (a.k.a TeaHost) support
- Added HikkaHost support
- Reworked 'grep' logic
- Added quotes for media in LegacyInfo, TesterMod
- Fixed help for libs
- Updated LegacyInfo default text
- Added ability to hide module commands in Help
- Updated Legacy-TL-New
- Added Banana PI support
- Added new keywoard '{tlversion}' in LegacyInfo
- Removed EULA for HikkaHost 
- Fixed 'InlineCall has no attr sender_id'
- Fixed output in Eval
- Some cosmetic changes
- Switched to aiogram 3.22
- Added inline.invoice method
- Fixed some bugs
- Fixed types
- Added new strings for legacycmd
- Moved from asset chats to a single forum with topics
- Added aiogram version in legacycmd
