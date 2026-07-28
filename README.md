# Community Donation Matcher — MVP

A lightweight, hyperlocal system that matches anyone with something to give away
— food, clothes, books, electronics, anything — to nearby NGOs/shelters in real
time, entirely over WhatsApp chat. **Donors and NGOs register themselves and post
donations without you doing anything manually** — your only job once this is
deployed is monitoring that it's running and helping with occasional disputes.

This MVP runs fully on your laptop and includes a **browser test dashboard**
so you can try the entire flow before connecting a real WhatsApp number.

---

## 1. Run it (takes 2 minutes)

```bash
cd food-match
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** in your browser. You'll see a dashboard where you can:

1. Register a donor (restaurant/household) with a name, phone, and pincode
2. Register an NGO with a name, phone, and pincode
3. Post a surplus food offer as a donor — it auto-matches and "broadcasts"
   to every available NGO in the same pincode
4. Simulate an NGO replying `CONFIRM <offer_id>` — first NGO to claim wins,
   others are automatically blocked (no double-claiming)
5. View the simulated WhatsApp message log at the bottom

This is the **exact matching logic** that will run in production — the only
thing missing right now is a real WhatsApp connection, which sends/receives
messages instead of just logging them.

---

## 2. How the matching works

- Every offer is matched against NGOs marked `is_available = 1` in the same pincode.
- If you later expand beyond one pincode, `matching.py` also supports radius-based
  matching using latitude/longitude (haversine distance) — already built in, just
  needs lat/long populated when you register donors/NGOs.
- Claims are handled with an atomic SQL update (`UPDATE ... WHERE status='open'`)
  so two NGOs can never both win the same offer, even if they reply at the same second.

---

## 3. Project structure

```
food-match/
├── app.py                 # Flask server: API routes + WhatsApp webhook
├── db.py                  # SQLite schema (donors, ngos, offers, claims, message_log)
├── matching.py             # Pincode + radius matching, race-safe claiming
├── whatsapp_sender.py      # Sends real WhatsApp messages (needs your API credentials)
├── templates/
│   └── dashboard.html      # Browser test UI (no WhatsApp needed)
├── requirements.txt
└── foodmatch.db             # SQLite database (created automatically)
```

---

## 4. Going live with real WhatsApp (when you're ready to pilot)

You need a **WhatsApp Business Cloud API** account (free tier is enough for a pilot):

1. Go to [developers.facebook.com](https://developers.facebook.com) → create an app → add the "WhatsApp" product.
2. Meta gives you a **temporary access token**, a **test phone number**, and a **Phone Number ID**.
3. Set these as environment variables before running the app:
   ```bash
   export WHATSAPP_TOKEN="your_token_here"
   export WHATSAPP_PHONE_NUMBER_ID="your_phone_number_id"
   export WHATSAPP_VERIFY_TOKEN="pick_any_secret_string"
   ```
4. Deploy `app.py` somewhere with a public URL (Render, Railway, or PythonAnywhere
   all have free tiers — WhatsApp needs a real HTTPS URL, not localhost).
5. In the Meta developer console, set your webhook URL to `https://your-deployed-url/webhook`
   and the verify token to the same value as `WHATSAPP_VERIFY_TOKEN` above.
6. In `app.py`, uncomment the `send_whatsapp_message(...)` calls (currently commented
   out — right now messages are only logged for the dashboard demo) and import it
   from `whatsapp_sender.py`.

Meta's free tier covers plenty of message volume for a single-neighborhood pilot.

---

## 5. How the self-service chat flow works

Nobody needs you to register them. This is what happens when someone messages
your WhatsApp number cold:

**First-time user:**
```
User: hi
Bot:  👋 Welcome! Are you looking to:
      1. Donate something (food, clothes, books, anything)
      2. Register as an NGO/shelter to receive donations
User: 1
Bot:  Great! What's your name (or your shop/household name)?
User: Ramesh Kirana Store
Bot:  Thanks! What's your pincode/area code?
User: 400001
Bot:  🎉 You're registered as a donor! Reply DONATE anytime, or send a photo directly.
```

**Posting a donation (two ways in):**

*Way 1 — text first:*
```
User: DONATE
Bot:  What are you donating? 1. Food 2. Clothes 3. Books 4. Electronics 5. Other
User: 2
Bot:  Briefly describe what you're donating.
User: 5 bags of kids winter clothes
Bot:  Want to attach a photo? Send it now, or reply SKIP.
User: [sends photo]
Bot:  Where should NGOs pick this up from?
User: Shop 12, Station Road
Bot:  ✅ Posted! Donation #1 sent to 3 nearby NGO(s).
```

*Way 2 — photo first (just snap and send, more natural for most people):*
```
User: [sends photo with no text]
Bot:  Got the photo! What category is this? 1. Food 2. Clothes ...
User: 1
Bot:  Briefly describe what you're donating.
User: 10 packed lunch boxes
Bot:  Where should NGOs pick this up from?
User: Near bus stand, Shop 3
Bot:  ✅ Posted! Donation #2 sent to 2 nearby NGO(s).
```

Photos are automatically downloaded from the donor's message and re-uploaded to
your WhatsApp business number so they can be forwarded to matched NGOs as part
of the broadcast — the NGO sees the actual photo, not just text.

**NGO side, anytime:**
- `AVAILABLE` / `UNAVAILABLE` — toggle whether they currently receive alerts
- `CONFIRM <id>` — claim a donation (first NGO to reply wins, race-safe)
- `MENU` or `HELP` — see their options again
- `RESET` — cancel a flow they're stuck in

Both onboarding flows use pincode as the matching key, so a donor and NGO only
get matched if their pincodes are the same (or within radius, if you populate
lat/long later).

---

## 6. Your actual day-to-day job once this is running

Since onboarding and posting are now self-service, your role shifts from
"data entry" to **light operations**:

1. **Watch the message log** (dashboard bottom panel, or `/api/messages`) for
   people getting stuck — e.g. someone sending a pincode with letters in it.
   The bot handles invalid input gracefully, but check in occasionally.
2. **Seed the network initially.** The bot can't matchmake with zero NGOs
   registered — you still need to personally message your first 3-5 NGO
   contacts (see the earlier "where do I get NGO contacts" advice) and get
   them to send "hi" to your WhatsApp number to self-register. Same for a
   handful of donors.
3. **Handle no-shows manually for now.** If an NGO claims a donation and never
   picks it up, there's no automated penalty yet — you'll need to notice this
   from complaints and follow up directly. The `claims` table has a `no_show`
   column ready for you to build a reliability score once you see how often
   this actually happens.
4. **Keep your WhatsApp access token fresh.** Meta's temporary tokens expire
   in ~24 hours during testing — once you're piloting for real, apply for a
   **permanent token** (Meta docs call this a "System User access token") so
   you're not manually refreshing it daily.
5. **Check server uptime.** If you deploy to Render/Railway free tier, free
   instances sometimes sleep after inactivity — the first message after a
   sleep period may take a few extra seconds to get a reply. Fine for a small
   pilot; worth upgrading if it becomes a problem.

You are not manually relaying messages anymore — that manual "concierge" step
was only for the *validation* phase before this bot existed. Once it's deployed
and people know the WhatsApp number, they operate independently.

---

## 7. Suggested pilot plan (from our earlier conversation)

1. **Pick one neighborhood.** Don't try to cover a whole city.
2. **Find 3-5 real NGO contacts** using NGO Darpan, local ward office, temples/gurudwaras,
   or by calling shelters/old-age homes found on Google Maps.
3. **Manually relay 2 weeks of offers** between donors and NGOs yourself (no code) to
   validate real demand and response speed before fully automating.
4. **Onboard everyone with the same pincode first** — matching only works if both
   sides are registered in overlapping pincodes.
5. Track **no-shows** (NGO confirms but doesn't arrive) — this is the #1 trust-breaker.
   The `claims` table already has a `no_show` column ready for you to build a simple
   reliability score later.

---

## 8. What's intentionally NOT built yet (by design, for a lean MVP)

- No AI/NLP — replies are structured commands and numbered menus, because
  free-text parsing is unreliable with real users early on.
- Conversation state lives in memory (`CONVERSATIONS` dict in `app.py`) — if the
  server restarts mid-conversation, that one user has to start their flow over.
  Fine for a small pilot; move this to a database table before scaling up.
- No authentication — fine for a single-neighborhood pilot you're personally running;
  add proper auth before scaling to multiple pilots.
- No profanity/spam moderation on donation descriptions or photos — add this if
  it becomes an issue during the pilot, not before.
- No reliability/rating system for NGOs yet — the `no_show` column exists in
  the schema, ready for you to build this once you see real no-show patterns.

Build these only once your pilot tells you they're actually needed — that's the
whole point of shipping lean.
