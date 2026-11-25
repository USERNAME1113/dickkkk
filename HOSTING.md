# הרצה ב-bot-hosting.net

## 1. יצירת שרת
1. היכנס ל-https://bot-hosting.net
2. Create server → בחר Python
3. המתן שהשרת יופיע ב-Panel

## 2. העלאת קבצים
העלה את **כל** תוכן ה-ZIP לתיקיית השרת:
- main.py
- black_palace_discord_bot.py
- requirements.txt
- runtime.txt (אם נתמך)

## 3. משתני סביבה (Environment)
בפאנל → Startup / Environment:

```
DISCORD_BOT_TOKEN=הטוקן_של_הבוט
OWNER_IDS=האיידי_שלך_בדיסקורד
```

(אופציונלי) OWNER_IDS=123,456 לכמה משתמשים

## 4. Startup command
```
python main.py
```
או אם הפאנל שואל Startup File:
```
main.py
```

## 5. Install dependencies
אם יש כפתור "Install" / console:
```
pip install -r requirements.txt
```
בד"כ bot-hosting מתקין אוטומטית מ-requirements.txt

## 6. Start
לחץ Start. בלוג אמור להופיע:
```
[env] token_set=True
Logged in as ...
```

## בעיות נפוצות
| בעיה | פתרון |
|------|--------|
| Missing DISCORD_BOT_TOKEN | שים env בפאנל, לא רק בקובץ |
| ModuleNotFoundError aiohttp | pip install -r requirements.txt |
| 403 Cloudflare על CAP | IP של שרת נחסם — חלק מהאתרים יעבדו רק מ-IP ביתי |
| הבוט נכבה אחרי 4 ימים | bot-hosting חינמי דורש חידוש ידני |

## קבצים חובה
main.py
black_palace_discord_bot.py
requirements.txt
