# Chat navigation and future HTML widget

## Release contract

`TELEGRAM_HTML_WIDGET_ENABLED=false` is the only supported configuration in this
release. `Settings.telegram_html_widget_enabled` defaults to `False`; `True` must
raise a clear startup validation error before workers or Telegram registration
start. A valid false configuration sends ordinary HTML messages with inline
callbacks. It must never probe `sendRichMessage` or call Widget API methods.

The Mini App remains available at its existing route as an independent application.
Chat screens and notifications no longer advertise or launch it. Product naming
remains TaskFlow. Business operations continue through the existing services,
transactions, version checks, operation keys and immutable score events.

## Entry-point inventory

| Area at the start of implementation | Required chat behavior |
| --- | --- |
| `app/bot/navigation.py`, legacy `launch_miniapp`, `/start` | Remove reply keyboard and open the existing chat menu. |
| `app/bot/handlers/common.py`, start handler | Use the same menu destination; no HTTPS or Mini App requirement. |
| `app/services/ui.py`, `menu` | Preserve callback entries for task creation, penalties and settings. |
| `app/services/ui.py`, `task_details` | Preserve task actions and navigation; remove application launch button. |
| `app/notifications/messages.py`, `notification_link` | Use `ratings`, `task`, `penalty_list` or `menu` callbacks. |
| `app/web/miniapp.py`, `configure_miniapp_menu`; application startup | Replace global and both private-chat WebApp menus with command menus. |
| `app/bot/navigation.py`, command registration | Install the declared commands globally and for both configured chats. |
| `app/bot/dispatcher.py`, message fallback | Render chat recovery/menu guidance without Mini App instructions. |
| `app/bot/render.py`, automatic rich send/edit fallback | Use `send_message` and `edit_message_text` with `parse_mode="HTML"`. |

Notification callbacks are untrusted navigation inputs. Existing access middleware
must still enforce the configured sender in the same private chat; destination
services load current state and enforce visibility. Mutations retain their normal
version and confirmation requirements. A notification tap during a form uses the
existing interruption confirmation rather than silently discarding a draft.

## Known-message cleanup boundary

The durable `Dialog` table stores an FSM JSON object containing `message_id` and
`screen_markup`. Those IDs can identify a previous bot screen with a cached
`web_app` button. Cleanup must be restricted to this bot's default dialog keys for
the two configured users in their matching private chats, and to markup that
actually contains a WebApp button. Remove its keyboard through Telegram's edit
method, handling inaccessible/deleted/too-old messages without blocking startup.
No history scan is needed or allowed.

The previous one-time launch CTA did not persist its sent message ID. Outbox
notifications also did not persist the returned Telegram message ID. Consequently
those old keyboards cannot all be located retrospectively. Replacing global and
personal menu buttons prevents new menu launches, but cannot erase unknown old
messages. Telegram may reject edits to known messages. Record these limitations
and verify real-client behavior separately from automated fake-bot tests.

## Official API compatibility review — 2026-11-26

Widgets are an actual API, distinct from both text HTML and Rich Messages. Telegram
introduced `sendWidget`, `WidgetQuery`, `answerWidgetQuery` and widget-data editing
in Bot API 10.4 on November 9, 2026. Production currently supports private-chat
widgets and protects them from forwarding. A widget has HTML code and separate
state; sending ordinary text with `parse_mode="HTML"` does not create one.
See the [official Bot API reference](https://core.telegram.org/bots/api#sendwidget)
and [API changelog](https://core.telegram.org/bots/api-changelog).

The [Widgets guide](https://core.telegram.org/bots/widgets) describes a sandboxed
HTML/CSS/JavaScript document with no network access. Code is immutable after send;
the bot updates state. Queries must receive an answer within 15 seconds. The HTML
limit is 32 KB, with at most 4 KB after compression. A future implementation needs
authenticated widget-query handling and state/version validation, plus real-client
compatibility tests. Static browser mockups are design artifacts, not proof that
Widget API integration works.

[Rich Messages](https://core.telegram.org/bots/api#sendrichmessage) use a separate
`InputRichMessage` model and send method. Their structured formatting does not
provide the interactive widget runtime. Therefore the existing automatic rich
fallback must not stand in for the disabled widget.

The repository pins aiogram **3.31.0** in `requirements.txt`; inspection of the
installed version confirms `Bot.send_rich_message` exists and `Bot.send_widget`
does not. Official [aiogram 3.34 widget documentation](https://docs.aiogram.dev/en/v3.34.0/api/methods/send_widget.html)
documents `SendWidget` and `Bot.send_widget`. A later Widget API release must
deliberately upgrade and validate its dependency and update pipeline. No dependency
upgrade or widget activation is needed for this chat-only release.

## Release evidence boundaries

Automated checks can establish output shape, command/menu registration calls,
configuration rejection, FSM safety and domain behavior. They cannot establish
the appearance of the real Telegram command menu, editability of historical
messages, device widget compatibility, or production delivery and persistence.
Merge and production deployment are outside this change. Earlier unfinished
rollout evidence must not be treated as confirmation of this release.
