# WhatsApp Cloud API — Faithful Mock Reference

> Spec our `MockWhatsAppProvider` implements so swapping to the real Meta Cloud
> API is a config change (`WHATSAPP_PROVIDER=meta`). Compiled from Meta's
> official docs (July 2026).

**Current stable Graph API version:** `v25.0`. Base URL: `https://graph.facebook.com`.

---

## 1. Inbound webhook — text message
`POST` → callback URL. Top-level `object` = `"whatsapp_business_account"`; payload at `entry[].changes[].value`; `changes[].field` = `"messages"`.

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<WABA_ID>",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550783881", "phone_number_id": "106540352242922"},
        "contacts": [{"profile": {"name": "Sheena"}, "wa_id": "16505551234"}],
        "messages": [{
          "from": "16505551234",
          "id": "wamid.HBgLMT...",
          "timestamp": "1749416383",
          "type": "text",
          "text": {"body": "Does it come in another color?"}
        }]
      },
      "field": "messages"
    }]
  }]
}
```
- `wa_id` / `messages[].from` = E.164 **without** leading `+`.
- `timestamp` = **string** epoch seconds.
- inbound `wamid` is referenced by a reply's `context.id`.

## 2. Inbound interactive replies
`messages[].type = "interactive"`, nested `interactive.type` = `button_reply` | `list_reply`, usually with a `context` pointing to the outbound message.

```json
{"type": "interactive", "interactive": {"type": "button_reply",
  "button_reply": {"id": "accept-food-change", "title": "Accept"}}}
```
```json
{"type": "interactive", "interactive": {"type": "list_reply",
  "list_reply": {"id": "menu-item-2", "title": "Paneer Butter Masala", "description": "..."}}}
```
- The echoed `id` = exactly the `id` set when sending buttons/list → **primary routing key**.
- Template quick-reply taps arrive instead as `type: "button"` with `button.payload`/`button.text`.

## 3. Verification handshake (GET)
`GET /webhook?hub.mode=subscribe&hub.challenge=1158201444&hub.verify_token=<token>`
- If `hub.verify_token` matches config → respond `200` with **raw** `hub.challenge` as plain text. Else `403`.

## 4. `X-Hub-Signature-256`
`X-Hub-Signature-256: sha256=<hex>` = HMAC-SHA256(App Secret, **raw body bytes**). Compute before any parse/reserialize; constant-time compare.

## 5. Outbound send API
`POST https://graph.facebook.com/v25.0/<PHONE_NUMBER_ID>/messages` + `Authorization: Bearer <ACCESS_TOKEN>`. Envelope: `messaging_product:"whatsapp"`, `recipient_type:"individual"`, `to` E.164.

**5a. text**
```json
{"messaging_product":"whatsapp","to":"+16505551234","type":"text","text":{"preview_url":true,"body":"..."}}
```
**5b. reply buttons** (1–3; title ≤20 chars; id ≤256, unique)
```json
{"type":"interactive","interactive":{"type":"button","body":{"text":"..."},
  "action":{"buttons":[{"type":"reply","reply":{"id":"b1","title":"Button 1"}}]}}}
```
**5c. list** (≤10 rows total; button label ≤20; row title ≤24, desc ≤72)
```json
{"type":"interactive","interactive":{"type":"list","body":{"text":"..."},
  "action":{"button":"Select","sections":[{"title":"Menu","rows":[{"id":"menu-item-1","title":"Butter Roti","description":"₹20"}]}]}}}
```
**5d. location**
```json
{"type":"location","location":{"latitude":"12.9719","longitude":"77.6412","name":"Sharma's Kitchen","address":"Indiranagar"}}
```
**5e. response** `200`:
```json
{"messaging_product":"whatsapp","contacts":[{"input":"+16505551234","wa_id":"16505551234"}],
 "messages":[{"id":"wamid.HBg..."}]}
```
Mock mints a unique `wamid` per send; reuse it in status webhooks.

## 6. Status webhooks
Same webhook, `value.statuses[]` instead of `messages[]`: `{id, status, timestamp, recipient_id}` with `status` cycling `sent`→`delivered`→`read` (`failed` adds `errors[]`).

## 7. Flows launch
`interactive.type="flow"`, `action.name="flow"`, `parameters.flow_message_version="3"`, `flow_id`|`flow_name`, `flow_cta`, `flow_action` (`navigate`+`flow_action_payload.screen` | `data_exchange`), `flow_token` correlation. Completion returns webhook `interactive.type="nfm_reply"` with `nfm_reply.response_json`.

## Cheat-sheet
- One version constant `v25.0`; only base URL + version change for real Meta.
- Inbound numbers no `+`; outbound `+` ok. `timestamp` = string epoch.
- Preserve routing keys: outbound button/list `id` → inbound `*_reply.id`; send `wamid` → status `id`.
- Sign every mock webhook with `X-Hub-Signature-256`; honor GET `hub.challenge`.

Sources: developers.facebook.com/docs/whatsapp/cloud-api (webhooks/payload-examples, guides/send-messages, flows/gettingstarted/sendingaflow), graph-api/webhooks/getting-started.
