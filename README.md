# WhatsApp Staff Register Bot

This bot reads the embedded JSON register extracted from `staff_register_portal.html`.
It contains 491 staff records and replies to a WhatsApp message containing a staff number.

## Files
- `app.py`: Flask WhatsApp Cloud API webhook.
- `staff_data.json`: exact embedded staffData array from the HTML upload.
- `requirements.txt`: Python dependencies.
- `Dockerfile`: cloud deployment configuration.

## Run locally
```bash
pip install -r requirements.txt
python app.py
```

## Railway variables
Set these in Railway > your service > Variables:
- `VERIFY_TOKEN`: create a long random string; enter the same value in Meta webhook setup.
- `META_APP_SECRET`: Meta app secret (for webhook signature verification).
- `WHATSAPP_ACCESS_TOKEN`: WhatsApp Cloud API access token.
- `PHONE_NUMBER_ID`: Phone Number ID from Meta.
- `GRAPH_API_VERSION`: `v27.0` (change if your Meta app uses another supported version).
- `ALLOWED_SENDERS`: your personal WhatsApp number in international digits only, e.g. `919876543210`.
- `DATA_FILE`: `staff_data.json`.

Only senders in `ALLOWED_SENDERS` can query the register. If you leave it blank, all senders will be accepted; set it before production.

## Meta webhook
- Callback URL: `https://YOUR-RAILWAY-DOMAIN/webhook`
- Verify token: same value as `VERIFY_TOKEN`
- Subscribe to the `messages` webhook field.

## Data notes
The HTML's embedded JSON has 491 rows. It has duplicate staff numbers:
- 821927 occurs at serials 229 and 487.
- 803701 occurs at serials 288 and 326.

The bot returns all matches for duplicate numbers rather than arbitrarily choosing one. The bot includes serial, name, staff number, member number, date, status, receipt number and amount.

The JSON is extracted from the uploaded HTML exactly; later edits in the HTML browser's local storage are not automatically included. If you update the register, replace `staff_data.json` with a fresh export from the current data.

## Privacy
The JSON contains staff personal information. Keep the GitHub repository private, restrict Railway access, and do not publish the JSON or expose the bot to unapproved senders.
