# iOS Share Sheet Shortcut — Second Brain Capture

One shortcut works for James (iPad + iPhone) and one for Kallan (iPhone).
Takes ~3 minutes to set up. Works from Safari, YouTube app, Chrome, Reddit, anywhere.

---

## What you need
- Firestore API key: `AIzaSyCTLN0v2LihL9o8Wvj9LDoyrVMNsdC4mkw`
- Firestore URL: `https://firestore.googleapis.com/v1/projects/forge-game/databases/(default)/documents/actions`

---

## Setup (do this on iPhone/iPad — Shortcuts app)

1. Open **Shortcuts** app
2. Tap **+** (top right)
3. Tap **Add Action**
4. Search: **"Receive"** → select **"Receive input from Share Sheet"**
   - Input type: **URLs** ✅ and **Text** ✅
   - If there's no input: **Continue**

5. Tap **+** → search **"Get Details of URLs"**
   - Select **Shortcut Input** as the URL

6. Tap **+** → search **"Get Current Date"**

7. Tap **+** → search **"Text"** → add this text block:
   ```
   {
     "fields": {
       "type": {"stringValue": "clip_url"},
       "url": {"stringValue": "[URL from step 5]"},
       "who": {"stringValue": "james"},
       "ts": {"stringValue": "[Date from step 6]"}
     }
   }
   ```
   Replace `[URL from step 5]` with the variable from step 5 (tap the field, insert variable)
   Replace `[Date from step 6]` with the date variable (ISO 8601 format)

   **For Kallan's shortcut:** change `"james"` to `"kallan"`

8. Tap **+** → search **"Get Contents of URL"** (this makes the HTTP request)
   - URL: `https://firestore.googleapis.com/v1/projects/forge-game/databases/(default)/documents/actions?key=AIzaSyCTLN0v2LihL9o8Wvj9LDoyrVMNsdC4mkw`
   - Method: **POST**
   - Request Body: **JSON** — paste the Text from step 7
   - Headers: Add `Content-Type` = `application/json`

9. Tap **+** → search **"Show Notification"**
   - Title: `📎 Sent to Second Brain`
   - Body: `Processing... check MeliNet in ~30 seconds`

10. Tap the shortcut name at top → rename to **"Save to Second Brain"**
    (For Kallan: "Kallan → Second Brain")

11. Tap the **ⓘ** → enable **"Show in Share Sheet"**

---

## How to use
- In Safari/YouTube/any app: tap **Share** → **"Save to Second Brain"**
- You'll get a notification immediately
- Within 30 seconds your home server processes it and sends MeliNet confirmation
- It's now searchable in the second brain

---

## Easier alternative: Web page shortcut
If the Shortcuts steps feel complex, there's a simpler option:

1. Open `https://jamescschoonover.github.io/Game-Of-Life/clip.html` on your phone
   (we'll build this page — it has a URL input box + Submit button)
2. Paste any URL → tap Capture
3. Same result, no Shortcuts setup needed
4. Add to Home Screen for one-tap access

---

## Adding a note when clipping
The shortcut can ask for an optional note before submitting.
Add a **"Ask for Input"** action before step 7:
- Prompt: "Why are you saving this? (optional)"
- Input type: Text
- Allow empty: Yes

Then include it in the JSON: `"note": {"stringValue": "[Ask for Input result]"}`
