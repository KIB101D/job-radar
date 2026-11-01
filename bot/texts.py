"""
Every user-facing bot text, in one place (Ukrainian, Telegram HTML).

Telegram has no coloured buttons, borders or progress bars, so the design is
carried by text formatting, emoji and plain keyboard buttons.
"""

# --- Reply keyboard (persistent menu) -------------------------------------------------

BTN_CHECK = "🔄 Перевірити зараз"
BTN_LATEST = "🗂 Останні 10"
BTN_FILTERS_MENU = "🔎 Фільтри"
BTN_NOTIFY_MENU = "⏰ Сповіщення"
BTN_PAUSE = "⏸ Пауза"
BTN_RESUME = "▶️ Відновити"
# Buttons of earlier menus: chats may still show them until the menu refreshes.
BTN_SETTINGS = "⚙️ Налаштування"
BTN_FREQUENCY = "⏰ Частота"
BTN_FILTERS = "⚙️ Змінити фільтри"
MENU_PLACEHOLDER = "Обери дію в меню"

# --- Commands (shown in Telegram's "/" menu) -------------------------------------------

COMMANDS = [
    ("check", "Перевірити нові вакансії зараз"),
    ("latest", "Останні 10 збігів"),
    ("filters", "Фільтри пошуку"),
    ("frequency", "Як часто надсилати нове"),
    ("pause", "Пауза або відновити сповіщення"),
    ("help", "Що вміє бот"),
    ("stop", "Відключити бота від радару"),
]

# --- Linking ------------------------------------------------------------------------------

WELCOME = (
    "👋 <b>Радар підключено!</b>\n"
    "Шукаю за фільтрами з сайту: {filters}.\n\n"
    "Меню внизу: перевірити нове вручну, подивитись останні 10, змінити фільтри "
    "чи частоту сповіщень або поставити їх на паузу."
)
WELCOME_BACK = "👋 <b>Ти вже підключений.</b>\nШукаю за фільтрами: {filters}."
START_NO_TOKEN = (
    "👋 Я бот <b>Job Radar</b>: надсилаю нові IT-вакансії з DOU за твоїми фільтрами.\n\n"
    "Щоб почати, налаштуй фільтри на сайті і натисни «Підключити бота»:\n{url}"
)
START_UNKNOWN_TOKEN = (
    "🤔 Не знайшов такий радар. Можливо, посилання скопіювалось не повністю.\n\n"
    "Відкрий сайт, налаштуй фільтри і натисни «Підключити бота»:\n{url}"
)
NOT_LINKED = (
    "Цей чат ще не підключено до радару. "
    "Налаштуй фільтри на сайті і натисни «Підключити бота»:\n{url}"
)
PRIVATE_ONLY = "Я працюю лише в особистих повідомленнях."

FILTERS_ALL = "усі вакансії"
FILTERS_EXCLUDE = "без: {words}"
FILTERS_REMOTE = "тільки віддалено"

# --- Vacancy lists ---------------------------------------------------------------------------

FIRST_BATCH = "🎯 <b>Найсвіжіші збіги</b> · {shown} з {total}"
FIRST_BATCH_EMPTY = "За твоїми фільтрами зараз нічого немає. Щойно з'явиться, надішлю."
MORE_BATCH = "🎯 <b>Ще збіги</b> · {start}–{end} з {total}"
MORE_BUTTON = "Показати ще"
LATEST = "🗂 <b>Останні {shown} збігів</b> з {total}"
LATEST_EMPTY = "За твоїми фільтрами зараз нічого немає."
LIST_MORE_ON_SITE = "…і ще {count} на сайті: {url}"
REMOTE_MARK = "🏠 віддалено"

# --- Check now ----------------------------------------------------------------------------

CHECK_WAIT = "⏳ Наступна перевірка через {seconds} с."
CHECK_FETCHING = "🔄 Перевіряю DOU…"
CHECK_FOUND = "🔄 <b>{count_text}</b>"
CHECK_NOTHING = "Нових вакансій немає."
CHECK_NEXT = "Наступне автосповіщення {when}."
CHECK_OFF = "Автосповіщення вимкнено, тож перевіряй вручну, коли зручно."
CHECK_PAUSED = "Автосповіщення на паузі."

# --- Digests ----------------------------------------------------------------------------------

DIGEST_HEADER = "📬 <b>{count_text}</b> за твоїми фільтрами"
DIGEST_NEXT = "⏰ Наступний дайджест {when}."

# --- Frequency & pause ------------------------------------------------------------------------

FREQUENCY_TITLE = "⏰ <b>Як часто надсилати нове?</b>"
FREQUENCY_CURRENT = "Зараз: <b>{label}</b>."
FREQUENCY_NEXT = "Наступне сповіщення {when} ({tz})."
FREQUENCY_OFF_HINT = "Перевіряй вручну кнопкою «{button}»."
FREQUENCY_PAUSED = "⏸ Сповіщення на паузі. Натисни «{button}» у меню, щоб відновити."
QUIET_ON = (
    "🌙 Тихі години {start}–{end}: вночі мовчу, все нове приходить одним повідомленням зранку."
)
QUIET_OFF = "🌙 Тихі години вимкнено: пишу навіть уночі."
QUIET_BUTTON_ON = "🌙 Тихі години: увімкнено"
QUIET_BUTTON_OFF = "🌙 Тихі години: вимкнено"
FREQUENCY_SAVED = "Збережено"
SELECTED = "✅ "

PAUSED = "⏸ <b>Сповіщення на паузі.</b>\nРучна перевірка працює як завжди."
RESUMED = "▶️ <b>Сповіщення відновлено.</b>"

# --- Filters, help, stop ------------------------------------------------------------------------

# --- Settings panel (one message, edited in place) ---------------------------------------------

LINE_WORDS = "✏️ Ключові слова: {value}"
LINE_EXCLUDE = "🚫 Виключені слова: {value}"
LINE_CATEGORIES = "📂 Категорії: {value}"
LINE_LEVELS = "📶 Рівень: {value}"
LINE_PLACES = "📍 Місто: {value}"
LINE_SALARY = "💵 Зарплата: {value}"
ANY_LEVEL = "будь-який"
ANY_PLACE = "будь-де"
ANY_SALARY = "будь-яка"
REMOTE = "віддалено"
SALARY_FROM = "від ${amount}"
SALARY_AND_UNKNOWN = "{salary} і без зарплати"
ANY_WORDS = "будь-які"
NO_EXCLUDE = "немає"
ALL_CATEGORIES = "усі"
YES = "так"
NO = "ні"
MATCHES = "📊 Зараз збігів: <b>{count}</b>"

FILTERS_TITLE = "🔎 <b>Фільтри</b>"
WORDS_TITLE = "✏️ <b>Ключові слова</b>"
WORDS_HELP = "Підійде вакансія, де є хоча б одне з них. Натисни слово, щоб прибрати."
WORDS_EMPTY = "Поки жодного, тож показую всі вакансії."
EXCLUDE_TITLE = "🚫 <b>Виключені слова</b>"
EXCLUDE_HELP = "Вакансії з цими словами не показую. Натисни слово, щоб прибрати."
EXCLUDE_EMPTY = "Поки жодного."
ADD_HINT = "➕ Щоб додати, напиши в чат, можна кілька через кому: <code>{example}</code>"
CATEGORIES_TITLE = "📂 <b>Категорії DOU</b>"
CATEGORIES_HELP = "Натисни, щоб увімкнути чи вимкнути. Якщо нічого не вибрано, шукаю в усіх."

BTN_SITE = "🌐 Відкрити на сайті"
BTN_CLOSE = "✖️ Закрити"
BTN_BACK_FILTERS = "← До фільтрів"
BTN_WORDS = "✏️ Ключові слова"
BTN_EXCLUDE = "🚫 Виключені слова"
BTN_CATEGORIES = "📂 Категорії"
BTN_REMOTE_ON = "🏠 Віддалено: ✅"
BTN_REMOTE_OFF = "🏠 Віддалено: ❌"
BTN_LEVELS = "📶 Рівень"
BTN_PLACES = "📍 Місто"
BTN_SALARY = "💵 Зарплата"
BTN_REMOTE_CHIP = "🏠 Віддалено"
BTN_ANY_LEVEL = "Будь-який рівень"
BTN_ANY_PLACE = "Будь-де"
BTN_ANY_SALARY = "Будь-яка"
BTN_SALARY_FROM = "від ${amount}"
BTN_UNKNOWN_ON = "Без зарплати: показувати ✅"
BTN_UNKNOWN_OFF = "Без зарплати: ховати"

LEVELS_TITLE = "📶 <b>Рівень</b>"
LEVELS_HELP = "Беру з назви вакансії. Можна кілька. Якщо рівня в назві немає, вакансію показую."
PLACES_TITLE = "📍 <b>Місто</b>"
PLACES_HELP = "Підійде віддалена робота або будь-яке з вибраних міст. Нічого не вибрано: будь-де."
SALARY_TITLE = "💵 <b>Зарплата</b>"
SALARY_HELP = (
    "Мінімум у доларах. Зарплату на DOU вказує лише частина вакансій, "
    "тож решту можна показувати або ховати."
)
BTN_REMOVE_WORD = "✖️ {word}"
BTN_CLEAR_WORDS = "🗑 Прибрати всі"
BTN_ALL_CATEGORIES = "Усі категорії"

NOTE_ADDED = "✅ Додано: {words}"
NOTE_ALREADY = "Ці слова вже є."
NOTE_REMOVED = "Прибрано: {word}"
NOTE_CLEARED = "Список очищено."
NOTE_TOO_MANY = "⚠️ Не більше {limit} слів, тож додав лише частину або нічого."
NOTE_TOO_LONG = "⚠️ «{word}» задовге: максимум {limit} символів."
NOTE_STALE = "Список змінився, ось актуальний."

HELP = (
    "<b>Що я вмію</b>\n"
    "🔄 <b>Перевірити зараз</b>: нові вакансії, яких ти ще не бачив "
    "(не частіше ніж раз на хвилину)\n"
    "🗂 <b>Останні 10</b>: найсвіжіші збіги за фільтрами\n"
    "🔎 <b>Фільтри</b>: ключові та виключені слова, категорії, віддалена робота\n"
    "⏰ <b>Сповіщення</b>: як часто надсилати нове і тихі години\n"
    "⏸ <b>Пауза</b>: тимчасово не надсилати сповіщення\n\n"
    "/stop відключає бота від радару."
)
UNLINKED_ON_SITE = (
    "👋 Бота відключено від радару на сайті. Фільтри там збережено.\n"
    "Щоб підключити знову, натисни «Підключити бота» на сайті."
)
RADAR_DELETED = (
    "🗑 Радар видалено на сайті разом з фільтрами, тож бот більше нічого не надсилатиме.\n"
    "Щоб почати знову, створи радар на сайті і натисни «Підключити бота»."
)
STOPPED = (
    "👋 Бота відключено від радару. Фільтри на сайті збережено.\n"
    "Щоб підключити знову, натисни «Підключити бота» на сайті."
)
UNKNOWN = "Не зрозумів. Скористайся кнопками меню внизу або /help."
CALLBACK_EXPIRED = "Ця кнопка вже не активна."


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def new_vacancies(n: int) -> str:
    """'1 нова вакансія', '3 нові вакансії', '5 нових вакансій'."""
    return f"{n} {plural(n, 'нова вакансія', 'нові вакансії', 'нових вакансій')}"
