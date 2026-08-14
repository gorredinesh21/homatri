# WhatsApp Business Platform — Cloud API
## Start-to-First-Message Setup TODO (Direct Integration, No BSP)

> Goal: Connect our backend to WhatsApp Cloud API and successfully **send and receive** the first real WhatsApp message.
> Path chosen: Direct Meta Cloud API (you manage app creation, tokens, webhooks yourself — not going through a partner platform like Wati/360dialog/Twilio).

---

## 0. Things to Have Ready

### Business information
- [ ] Meta/Facebook account for the person managing the business
- [ ] Legal business name (must match registration/GST/incorporation docs exactly — mismatches are the #1 cause of verification rejection)
- [ ] Business/brand name
- [ ] Business address
- [ ] Business email
- [ ] Business phone number (can differ from the WhatsApp number)
- [ ] Business website
- [ ] Business description
- [ ] Business registration/verification documents, if requested by Meta
- [ ] **A payment method on file** — Cloud API requires a valid payment method attached to the WABA before you can send messages at scale (conversation-based pricing)

### WhatsApp phone number
- [ ] Dedicated business phone number
- [ ] Number can receive SMS or voice calls for verification
- [ ] Decide: brand-new number, or an existing WhatsApp/WhatsApp Business App number?
- [ ] **If existing WhatsApp number:** decide between
  - **Full migration** — deregister it from the WhatsApp/WhatsApp Business consumer app first, then register on Cloud API (you lose the consumer app)
  - **Coexistence** — some businesses can keep using the WhatsApp Business App *and* the Cloud API on the same number simultaneously; eligibility is region- and account-dependent, so check current eligibility in Meta Business Suite before assuming it's available
- [ ] Note: you don't strictly need your production number on day one — Meta gives you a **free test number** automatically (see Section 6a) that lets you send/receive your first message immediately

---

## 1. Create Meta Business Portfolio

- [ ] Go to https://business.facebook.com/
- [ ] Log in with the Meta account
- [ ] Create a Business Portfolio for the company (create it under a company-owned account, not a personal profile — ownership transfer later is painful)
- [ ] Enter real business information: legal name, email, address
- [ ] Complete any business verification requested by Meta (can take several business days — start this early since it's often the long pole)

### Keep these details
- [ ] Business Portfolio ID
- [ ] Business name

---

## 2. Create Meta Developer Account

Go to: https://developers.facebook.com/

- [ ] Log in with the Meta account
- [ ] Complete developer registration if prompted
- [ ] Confirm the developer account

---

## 3. Create a Meta App

Inside Meta for Developers:

- [ ] Go to **My Apps** → **Create App**
- [ ] Select the **Business** use case when prompted
- [ ] Associate the app with the Business Portfolio from Step 1
- [ ] Create the app
- [ ] From the App Dashboard, add the **WhatsApp** product

### Keep these details
- [ ] App ID
- [ ] App name
- [ ] App Secret — private, used later to verify webhook signatures (`X-Hub-Signature-256`)

---

## 4. WhatsApp Product Setup (Quickstart / API Setup panel)

Adding the WhatsApp product usually **auto-creates a test WABA and a free test phone number** for you — you don't have to build anything yet to get your first message flowing.

- [ ] Open **App Dashboard → WhatsApp → API Setup** (sometimes shown as "Quickstart")
- [ ] Confirm a WABA was auto-created and is linked to the app
- [ ] If not, connect/create one manually here

### Keep these details
- [ ] WhatsApp Business Account ID (WABA ID)
- [ ] Test Phone Number ID (temporary — for the free test number)

---

## 5. Send Your Actual First Message (fastest path — do this before production setup)

This is the step your original checklist never reached. Do this with the free test number to prove the pipeline works end-to-end before dealing with production numbers/tokens.

- [ ] In the API Setup panel, click **Generate access token** (temporary, 24h — testing only, never use in production)
- [ ] Select the auto-provisioned test number as the **From** number
- [ ] Add a **To** number: your own personal WhatsApp number, added as a recipient (test numbers can only message pre-approved recipient numbers)
- [ ] Click **Send message** — this fires a `POST` to `https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages` with a pre-built template payload
- [ ] Confirm you receive the message on WhatsApp
- [ ] Reply to it from your phone — this opens a 24-hour customer service window
- [ ] Confirm the reply lands somewhere you can see it (webhook not set up yet, so check the **Webhooks** tab's test event log, or proceed to Section 8 first if you want to see it hit your backend)

✅ At this point you've proven send + receive works end-to-end. Everything below is turning this into a real, production-ready integration.

---

## 6. Configure the WhatsApp Business Profile

Inside WhatsApp Manager (business.facebook.com → WhatsApp Manager) or via the API Setup panel:

- [ ] Confirm the WhatsApp Business Account
- [ ] Configure the business display name (subject to Meta approval, typically 24–72 hrs — avoid generic names like "Support" without a brand prefix; rejections are common)
- [ ] Configure the business profile: logo, description, business hours
- [ ] Add the business category
- [ ] Add address/website/email where prompted

---

## 6a. Add the Production WhatsApp Phone Number

- [ ] In WhatsApp Manager or API Setup, add the dedicated production phone number
- [ ] Enter the country code and number in E.164 format (e.g. `+919876543210`)
- [ ] Select the verification method offered (SMS or voice)
- [ ] Receive and enter the verification code
- [ ] Set the **two-step verification PIN** (six digits) — Meta requires this for number registration
- [ ] If migrating an existing WhatsApp number, complete deregistration/Coexistence steps decided in Section 0 here
- [ ] Confirm the number shows as successfully registered/connected

### Keep these details
- [ ] Production Phone Number ID
- [ ] Production WhatsApp phone number
- [ ] WABA ID (should match Section 4)

---

## 7. Configure API Authentication (Production Token)

Temporary tokens expire in 24 hours — not usable for a real backend. You need a **permanent System User token**.

- [ ] Go to Meta Business Suite → **Business Settings → Users → System Users**
- [ ] Create a System User (role: Admin, or Employee if scoping tightly) under the Business Portfolio
- [ ] Assign the System User to the WhatsApp App (from Step 3) and the WABA (from Step 4/6a) with the required assets
- [ ] Generate a token for the System User, scoped to the `whatsapp_business_messaging` (and `whatsapp_business_management` if you'll manage templates/numbers via API) permission(s)
- [ ] Set token expiration to **Never** (System User tokens can be permanent, unlike user tokens)
- [ ] Store the token securely (secrets manager — never in source control)

### Required configuration for backend

```env
WHATSAPP_ACCESS_TOKEN=...          # permanent System User token
WHATSAPP_PHONE_NUMBER_ID=...       # production Phone Number ID
WHATSAPP_BUSINESS_ACCOUNT_ID=...   # WABA ID
WHATSAPP_APP_SECRET=...            # used to verify webhook signatures
WHATSAPP_WEBHOOK_VERIFY_TOKEN=...  # a random string you choose, used in Section 8
```

---

## 8. Configure Webhooks (needed to actually *receive* messages)

Your original checklist stopped before this — without it you can send messages but never receive incoming ones or delivery/read status updates.

- [ ] Stand up a public HTTPS endpoint on your backend, e.g. `POST /webhooks/whatsapp` and `GET /webhooks/whatsapp` (same route, different verbs)
- [ ] Implement the **GET** handler: Meta calls this once with `hub.mode`, `hub.verify_token`, `hub.challenge` query params — check `hub.verify_token` matches `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, and if it does, respond with the raw `hub.challenge` value
- [ ] Implement the **POST** handler: receives incoming message/status payloads as JSON
- [ ] Validate every incoming POST using the `X-Hub-Signature-256` header (HMAC-SHA256 of the raw body using `WHATSAPP_APP_SECRET`) — reject anything that doesn't match
- [ ] Deploy this endpoint somewhere reachable over HTTPS (a tunnel like ngrok is fine for initial testing, not for production)
- [ ] In **App Dashboard → WhatsApp → Configuration**, set the **Callback URL** to your endpoint and the **Verify Token** to match your env var
- [ ] Click **Verify and Save** — Meta will hit your GET handler right away; this must succeed before webhooks are considered configured
- [ ] Subscribe the app to WABA webhook fields: at minimum `messages` (incoming messages) and `message_status` / `message_template_status_update` if you care about delivery/read receipts and template approvals
- [ ] Send a message to your production number from your own phone and confirm the payload arrives at your backend

---

## 9. Message Templates (required for business-initiated messages)

You can only freely reply within a 24-hour window after a user messages you. To message someone first (or after the window closes), you need an **approved template**.

- [ ] In WhatsApp Manager → **Message Templates**, create at least one template (e.g. a simple welcome/notification template)
- [ ] Choose category (Marketing / Utility / Authentication) — this affects approval scrutiny and pricing
- [ ] Submit for review (typically minutes to a few hours, occasionally longer)
- [ ] Confirm status is **Approved** before trying to send it via API

---

## 10. Production Readiness Checks

- [ ] Payment method attached to the WABA (Meta Business Suite → Payments)
- [ ] Understand messaging limits: new numbers start in a lower messaging tier (e.g. 250 unique users/24h) and scale up automatically based on quality rating and volume — don't plan a big-bang launch on day one
- [ ] Monitor the number's **Quality Rating** in WhatsApp Manager; a low rating can throttle or restrict sending
- [ ] Rotate/secure the System User token the same way you'd treat any other production secret
- [ ] Set up basic logging/alerting on webhook failures (a failed signature check or downtime here silently drops incoming messages)

---

## Summary of what changed from your original draft

| Gap | Fix |
|---|---|
| No webhook setup | Added Section 8 in full |
| Vague token step | Split into temporary (Section 5) vs. permanent System User token (Section 7) |
| No actual "send first message" step | Added Section 5, using the free auto-provisioned test number |
| No templates | Added Section 9 |
| Migration/Coexistence flagged but unresolved | Folded the decision into Section 6a with the actual mechanics |
| No production readiness / rate limits | Added Section 10 |