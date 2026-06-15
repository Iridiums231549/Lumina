import html
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import markdown
from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config.from_mapping(
    SECRET_KEY=os.environ.get("LUMINA_SECRET_KEY", "lumina-dev-secret-key"),
    DATABASE=os.environ.get(
        "LUMINA_DATABASE_PATH",
        os.path.join(app.instance_path, "lumina.db"),
    ),
    PER_PAGE=8,
)

APP_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

os.makedirs(app.instance_path, exist_ok=True)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_column(db, table_name, column_sql):
    column_name = column_sql.split()[0]
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def init_db():
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname TEXT DEFAULT '',
            avatar TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            slug TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            content TEXT NOT NULL,
            excerpt TEXT DEFAULT '',
            cover_image TEXT DEFAULT '',
            author_id INTEGER NOT NULL REFERENCES users(id),
            category_id INTEGER REFERENCES categories(id),
            status TEXT DEFAULT 'published',
            view_count INTEGER DEFAULT 0,
            is_top INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS article_tags (
            article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (article_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER NOT NULL REFERENCES users(id),
            article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, article_id)
        );

        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            kind TEXT DEFAULT 'notice',
            is_active INTEGER DEFAULT 1,
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER REFERENCES users(id),
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER,
            detail TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    ensure_column(db, "users", "status TEXT DEFAULT 'active'")
    ensure_column(db, "users", "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ensure_column(db, "users", "last_login_at TIMESTAMP")
    ensure_column(db, "articles", "is_hidden INTEGER DEFAULT 0")
    ensure_column(db, "comments", "is_hidden INTEGER DEFAULT 0")
    db.commit()

    seed_defaults(db)
    db.close()


def seed_defaults(db):
    try:
        db.execute(
            """
            INSERT INTO users (username, email, password_hash, nickname, role, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "admin",
                "admin@lumina.local",
                generate_password_hash("admin123"),
                "Lumina 管理员",
                "admin",
                "active",
            ),
        )
    except sqlite3.IntegrityError:
        pass

    if db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        categories = [
            ("未分类", "uncategorized", "默认分类"),
            ("技术", "tech", "开发技术与工程实践"),
            ("前端", "frontend", "界面设计、交互与体验"),
            ("后端", "backend", "服务端架构、数据库与接口"),
            ("校园随笔", "campus", "课程、社团与校园生活"),
        ]
        db.executemany(
            "INSERT INTO categories (name, slug, description) VALUES (?, ?, ?)",
            categories,
        )

    if db.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0:
        tag_names = ["Flask", "SQLite", "软件工程", "Markdown", "课程设计", "前端体验"]
        for tag_name in tag_names:
            slug = generate_slug(tag_name)
            db.execute("INSERT INTO tags (name, slug) VALUES (?, ?)", (tag_name, slug))

    try:
        db.execute(
            """
            INSERT INTO users (username, email, password_hash, nickname, bio, role, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "editor",
                "editor@lumina.local",
                generate_password_hash("editor123"),
                "Lumina 编辑部",
                "负责课程博客内容维护与运营公告发布。",
                "user",
                "active",
            ),
        )
    except sqlite3.IntegrityError:
        pass

    if db.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0:
        admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
        editor_id = db.execute("SELECT id FROM users WHERE username = 'editor'").fetchone()["id"]
        categories = {
            row["slug"]: row["id"] for row in db.execute("SELECT id, slug FROM categories").fetchall()
        }
        demo_articles = [
            (
                "Lumina 2.0：从课程原型到完整博客系统",
                "lumina-2-upgrade",
                """# Lumina 2.0

Lumina 现在已经从简易课程原型升级为一套更完整的博客系统，覆盖了：

- 用户注册、登录、资料维护与密码修改
- Markdown 文章创作、草稿箱与标签分类
- 评论回复、点赞互动与权限控制
- 后台公告、用户封禁、内容隐藏、统计与审计日志

## 为什么这样设计

课程设计验收需要的不只是“能发文章”，还需要能够支撑内容治理、权限边界和基础运营流程。新版系统围绕这些验收点进行了补强。
""",
                "围绕课程设计 SRS 的验收点，Lumina 已补齐内容治理、公告、统计和审计模块。",
                admin_id,
                categories["tech"],
                "published",
                1,
                186,
            ),
            (
                "用 Markdown 写一篇结构清晰的课程博客",
                "markdown-course-writing",
                """# 用 Markdown 写课程博客

Markdown 适合课程项目里的内容沉淀，它有三个优势：

1. 结构清晰，便于快速排版
2. 成本低，适合多人协作
3. 能同时兼顾阅读和后期维护

## 一个简单模板

```markdown
# 标题

## 背景
## 设计思路
## 实现细节
## 验收结果
```

持续写作会让项目过程更容易被追踪与复盘。
""",
                "Markdown 能帮助课程博客快速沉淀结构化内容，也更适合多人协作与后期维护。",
                editor_id,
                categories["frontend"],
                "published",
                0,
                92,
            ),
            (
                "后台治理需求清单",
                "admin-governance-checklist",
                """# 后台治理需求清单

这是一篇草稿，用于梳理管理员在课程验收中需要展示的能力：

- 公告发布
- 用户封禁与解封
- 文章隐藏与置顶
- 评论隐藏与删除
- 最近 7 日统计
- 审计日志追踪
""",
                "管理员需要具备基本治理能力，这篇草稿梳理了答辩中常见的展示点。",
                admin_id,
                categories["backend"],
                "draft",
                0,
                14,
            ),
        ]
        db.executemany(
            """
            INSERT INTO articles
            (title, slug, content, excerpt, author_id, category_id, status, is_top, view_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            demo_articles,
        )

        article_tags = {
            "lumina-2-upgrade": ["Flask", "软件工程", "课程设计"],
            "markdown-course-writing": ["Markdown", "课程设计", "前端体验"],
            "admin-governance-checklist": ["Flask", "SQLite", "软件工程"],
        }
        for slug, names in article_tags.items():
            article_id = db.execute("SELECT id FROM articles WHERE slug = ?", (slug,)).fetchone()["id"]
            update_article_tags(db, article_id, names)

    if db.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0:
        article_id = db.execute(
            "SELECT id FROM articles WHERE slug = 'lumina-2-upgrade'"
        ).fetchone()["id"]
        admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
        editor_id = db.execute("SELECT id FROM users WHERE username = 'editor'").fetchone()["id"]
        db.execute(
            "INSERT INTO comments (article_id, user_id, content) VALUES (?, ?, ?)",
            (article_id, editor_id, "这版把后台治理能力补齐之后，课程答辩展示会顺很多。"),
        )
        parent_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO comments (article_id, user_id, parent_id, content) VALUES (?, ?, ?, ?)",
            (article_id, admin_id, parent_id, "是的，尤其是公告、统计和日志追踪这几个模块。"),
        )

    if db.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 0:
        admin_id = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
        notices = [
            (
                "课程项目站点已升级",
                "Lumina 已完成核心功能补强，现在支持公告、审计日志、维护模式和后台治理。",
                "notice",
                1,
                admin_id,
            ),
            (
                "维护模式示例（默认关闭）",
                "当系统处于维护状态时，普通用户的写操作会暂时受限，管理员仍可继续治理内容。",
                "maintenance",
                0,
                admin_id,
            ),
        ]
        db.executemany(
            """
            INSERT INTO announcements (title, content, kind, is_active, created_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            notices,
        )

    db.commit()


def generate_slug(value):
    slug = re.sub(r"[^\w\s-]", "", (value or "").strip().lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug or f"post-{uuid.uuid4().hex[:8]}"


def render_markdown(text):
    safe_source = html.escape(text or "")
    return markdown.markdown(
        safe_source,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
    )


def build_excerpt(text, limit=140):
    plain = re.sub(r"[#>*`~\[\]\(\)\-_]", " ", text or "")
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit] + ("..." if len(plain) > limit else "")


def current_user():
    return getattr(g, "current_user", None)


def is_admin(user=None):
    user = user or current_user()
    return bool(user and user["role"] == "admin")


def get_public_categories(db):
    return db.execute(
        """
        SELECT c.*,
               (
                   SELECT COUNT(*)
                   FROM articles a
                   WHERE a.category_id = c.id
                     AND a.status = 'published'
                     AND COALESCE(a.is_hidden, 0) = 0
               ) AS article_count
        FROM categories c
        ORDER BY c.name
        """
    ).fetchall()


def get_active_notices(db, kind="notice", limit=3):
    notices = db.execute(
        """
        SELECT an.*, u.username, u.nickname
        FROM announcements an
        LEFT JOIN users u ON an.created_by = u.id
        WHERE an.kind = ? AND an.is_active = 1
        ORDER BY an.created_at DESC
        LIMIT ?
        """,
        (kind, limit),
    ).fetchall()
    enriched = []
    for notice in notices:
        item = dict(notice)
        item["html"] = render_markdown(item["content"])
        enriched.append(item)
    return enriched


def get_maintenance_notice(db):
    notice = db.execute(
        """
        SELECT * FROM announcements
        WHERE kind = 'maintenance' AND is_active = 1
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not notice:
        return None
    item = dict(notice)
    item["html"] = render_markdown(item["content"])
    return item


def update_article_tags(db, article_id, tag_names):
    db.execute("DELETE FROM article_tags WHERE article_id = ?", (article_id,))
    seen = set()
    for raw_name in tag_names:
        tag_name = raw_name.strip()
        if not tag_name:
            continue
        normalized = tag_name.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        tag_slug = generate_slug(tag_name)
        existing = db.execute("SELECT id FROM tags WHERE slug = ?", (tag_slug,)).fetchone()
        if existing is None:
            db.execute("INSERT INTO tags (name, slug) VALUES (?, ?)", (tag_name, tag_slug))
            tag_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            tag_id = existing["id"]
        db.execute(
            "INSERT OR IGNORE INTO article_tags (article_id, tag_id) VALUES (?, ?)",
            (article_id, tag_id),
        )


def record_audit(action, target_type, target_id=None, detail=""):
    db = get_db()
    actor_id = session.get("user_id")
    db.execute(
        """
        INSERT INTO audit_logs (actor_id, action, target_type, target_id, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (actor_id, action, target_type, target_id, detail),
    )
    db.commit()


def ensure_write_allowed():
    user = current_user()
    if user is None:
        flash("请先登录后再进行该操作。", "warning")
        return redirect(url_for("login", next=request.path))
    if user["status"] == "banned":
        flash("当前账号已被封禁，无法继续执行写操作。", "danger")
        return redirect(url_for("index"))
    maintenance_notice = get_maintenance_notice(get_db())
    if maintenance_notice and not is_admin(user):
        flash("系统正在维护中，普通用户的写操作已暂时关闭。", "warning")
        return redirect(request.referrer or url_for("index"))
    return None


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("请先登录。", "warning")
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            flash("请先登录。", "warning")
            return redirect(url_for("login", next=request.path))
        if user["role"] != "admin":
            flash("该页面仅管理员可访问。", "danger")
            return redirect(url_for("index"))
        return view_func(*args, **kwargs)

    return wrapped


@app.before_request
def prepare_request():
    if "_csrf_token" not in session:
        session["_csrf_token"] = uuid.uuid4().hex

    g.current_user = None
    user_id = session.get("user_id")
    if user_id:
        user = get_db().execute(
            """
            SELECT id, username, nickname, avatar, bio, email, role, status, created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if user and user["status"] == "banned":
            session.pop("user_id", None)
            flash("该账号已被管理员封禁。", "danger")
            return redirect(url_for("login"))
        g.current_user = user

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if token != session.get("_csrf_token"):
            if request.path.startswith("/like/") or request.path == "/preview-markdown":
                return jsonify({"error": "CSRF token invalid"}), 400
            flash("页面已过期，请刷新后重试。", "danger")
            return redirect(request.referrer or url_for("index"))


@app.context_processor
def inject_globals():
    db = get_db()
    maintenance_notice = get_maintenance_notice(db)
    return {
        "current_user": current_user(),
        "csrf_token": lambda: session.get("_csrf_token", ""),
        "site_name": "Lumina",
        "maintenance_notice": maintenance_notice,
    }


@app.template_filter("format_date")
def format_date(value, fmt="%Y-%m-%d %H:%M"):
    if value is None:
        return ""
    if isinstance(value, str):
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                value = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # SQLite CURRENT_TIMESTAMP stores UTC. Convert all stored timestamps
            # to Beijing time before rendering so the whole site stays consistent.
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(APP_TIMEZONE)
    return value.strftime(fmt) if hasattr(value, "strftime") else str(value)


def get_article_tags(db, article_id):
    return db.execute(
        """
        SELECT t.*
        FROM tags t
        JOIN article_tags at ON at.tag_id = t.id
        WHERE at.article_id = ?
        ORDER BY t.name
        """,
        (article_id,),
    ).fetchall()


def enrich_articles(db, articles):
    enriched = []
    for article in articles:
        item = dict(article)
        item["tags"] = get_article_tags(db, item["id"])
        item["status_label"] = (
            "已隐藏"
            if item.get("is_hidden")
            else "草稿"
            if item.get("status") == "draft"
            else "已发布"
        )
        enriched.append(item)
    return enriched


@app.route("/")
def index():
    db = get_db()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * app.config["PER_PAGE"]

    total = db.execute(
        """
        SELECT COUNT(*)
        FROM articles
        WHERE status = 'published' AND COALESCE(is_hidden, 0) = 0
        """
    ).fetchone()[0]

    articles = db.execute(
        """
        SELECT a.*, u.username, u.nickname, c.name AS category_name, c.slug AS category_slug,
               (
                   SELECT COUNT(*) FROM comments co
                   WHERE co.article_id = a.id AND COALESCE(co.is_hidden, 0) = 0
               ) AS comment_count,
               (
                   SELECT COUNT(*) FROM likes l WHERE l.article_id = a.id
               ) AS like_count
        FROM articles a
        JOIN users u ON u.id = a.author_id
        LEFT JOIN categories c ON c.id = a.category_id
        WHERE a.status = 'published' AND COALESCE(a.is_hidden, 0) = 0
        ORDER BY a.is_top DESC, a.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (app.config["PER_PAGE"], offset),
    ).fetchall()

    top_articles = db.execute(
        """
        SELECT id, title, slug, view_count
        FROM articles
        WHERE status = 'published' AND COALESCE(is_hidden, 0) = 0
        ORDER BY view_count DESC, created_at DESC
        LIMIT 5
        """
    ).fetchall()

    total_pages = max(1, (total + app.config["PER_PAGE"] - 1) // app.config["PER_PAGE"])
    return render_template(
        "index.html",
        articles=enrich_articles(db, articles),
        categories=get_public_categories(db),
        top_articles=top_articles,
        announcements=get_active_notices(db, "notice", 3),
        page=page,
        total_pages=total_pages,
        total_articles=total,
    )


@app.route("/article/<slug>")
def article_detail(slug):
    db = get_db()
    article = db.execute(
        """
        SELECT a.*, u.username, u.nickname, u.bio, c.name AS category_name, c.slug AS category_slug
        FROM articles a
        JOIN users u ON u.id = a.author_id
        LEFT JOIN categories c ON c.id = a.category_id
        WHERE a.slug = ?
        """,
        (slug,),
    ).fetchone()

    if article is None:
        flash("文章不存在。", "danger")
        return redirect(url_for("index"))

    allowed_to_preview = current_user() and (
        current_user()["id"] == article["author_id"] or is_admin()
    )
    is_public_article = article["status"] == "published" and article["is_hidden"] == 0
    if not is_public_article and not allowed_to_preview:
        flash("该文章当前不可访问。", "warning")
        return redirect(url_for("index"))

    if is_public_article:
        db.execute("UPDATE articles SET view_count = view_count + 1 WHERE id = ?", (article["id"],))
        db.commit()
        article = db.execute(
            """
            SELECT a.*, u.username, u.nickname, u.bio, c.name AS category_name, c.slug AS category_slug
            FROM articles a
            JOIN users u ON u.id = a.author_id
            LEFT JOIN categories c ON c.id = a.category_id
            WHERE a.id = ?
            """,
            (article["id"],),
        ).fetchone()

    comments = db.execute(
        """
        SELECT c.*, u.username, u.nickname
        FROM comments c
        JOIN users u ON u.id = c.user_id
        WHERE c.article_id = ? AND c.parent_id IS NULL AND COALESCE(c.is_hidden, 0) = 0
        ORDER BY c.created_at DESC
        """,
        (article["id"],),
    ).fetchall()
    replies = db.execute(
        """
        SELECT c.*, u.username, u.nickname
        FROM comments c
        JOIN users u ON u.id = c.user_id
        WHERE c.article_id = ? AND c.parent_id IS NOT NULL AND COALESCE(c.is_hidden, 0) = 0
        ORDER BY c.created_at ASC
        """,
        (article["id"],),
    ).fetchall()
    like_count = db.execute("SELECT COUNT(*) FROM likes WHERE article_id = ?", (article["id"],)).fetchone()[0]
    liked = False
    if current_user():
        liked = (
            db.execute(
                "SELECT 1 FROM likes WHERE user_id = ? AND article_id = ?",
                (current_user()["id"], article["id"]),
            ).fetchone()
            is not None
        )

    return render_template(
        "article_detail.html",
        article=article,
        article_tags=get_article_tags(db, article["id"]),
        article_html=render_markdown(article["content"]),
        comments=comments,
        replies=replies,
        like_count=like_count,
        liked=liked,
        comment_total=len(comments) + len(replies),
        can_preview_unpublished=allowed_to_preview and not is_public_article,
    )


@app.route("/category/<slug>")
def category(slug):
    db = get_db()
    category = db.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()
    if category is None:
        flash("分类不存在。", "danger")
        return redirect(url_for("index"))

    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * app.config["PER_PAGE"]
    total = db.execute(
        """
        SELECT COUNT(*)
        FROM articles
        WHERE category_id = ? AND status = 'published' AND COALESCE(is_hidden, 0) = 0
        """,
        (category["id"],),
    ).fetchone()[0]

    articles = db.execute(
        """
        SELECT a.*, u.username, u.nickname, c.name AS category_name, c.slug AS category_slug,
               (
                   SELECT COUNT(*) FROM comments co
                   WHERE co.article_id = a.id AND COALESCE(co.is_hidden, 0) = 0
               ) AS comment_count,
               (
                   SELECT COUNT(*) FROM likes l WHERE l.article_id = a.id
               ) AS like_count
        FROM articles a
        JOIN users u ON u.id = a.author_id
        LEFT JOIN categories c ON c.id = a.category_id
        WHERE a.category_id = ? AND a.status = 'published' AND COALESCE(a.is_hidden, 0) = 0
        ORDER BY a.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (category["id"], app.config["PER_PAGE"], offset),
    ).fetchall()

    total_pages = max(1, (total + app.config["PER_PAGE"] - 1) // app.config["PER_PAGE"])
    return render_template(
        "category.html",
        category=category,
        categories=get_public_categories(db),
        articles=enrich_articles(db, articles),
        page=page,
        total_pages=total_pages,
    )


@app.route("/search")
def search():
    db = get_db()
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * app.config["PER_PAGE"]

    articles = []
    total = 0
    if query:
        like_query = f"%{query}%"
        total = db.execute(
            """
            SELECT COUNT(*)
            FROM articles a
            WHERE a.status = 'published'
              AND COALESCE(a.is_hidden, 0) = 0
              AND (
                  a.title LIKE ?
                  OR a.content LIKE ?
                  OR a.excerpt LIKE ?
                  OR EXISTS (
                      SELECT 1
                      FROM article_tags at
                      JOIN tags t ON t.id = at.tag_id
                      WHERE at.article_id = a.id AND t.name LIKE ?
                  )
              )
            """,
            (like_query, like_query, like_query, like_query),
        ).fetchone()[0]
        articles = db.execute(
            """
            SELECT a.*, u.username, u.nickname, c.name AS category_name, c.slug AS category_slug,
                   (
                       SELECT COUNT(*) FROM comments co
                       WHERE co.article_id = a.id AND COALESCE(co.is_hidden, 0) = 0
                   ) AS comment_count
            FROM articles a
            JOIN users u ON u.id = a.author_id
            LEFT JOIN categories c ON c.id = a.category_id
            WHERE a.status = 'published'
              AND COALESCE(a.is_hidden, 0) = 0
              AND (
                  a.title LIKE ?
                  OR a.content LIKE ?
                  OR a.excerpt LIKE ?
                  OR EXISTS (
                      SELECT 1
                      FROM article_tags at
                      JOIN tags t ON t.id = at.tag_id
                      WHERE at.article_id = a.id AND t.name LIKE ?
                  )
              )
            ORDER BY a.is_top DESC, a.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (like_query, like_query, like_query, like_query, app.config["PER_PAGE"], offset),
        ).fetchall()

    total_pages = max(1, (total + app.config["PER_PAGE"] - 1) // app.config["PER_PAGE"]) if query else 1
    return render_template(
        "search.html",
        query=query,
        total=total,
        categories=get_public_categories(db),
        articles=enrich_articles(db, articles),
        page=page,
        total_pages=total_pages,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    maintenance_notice = get_maintenance_notice(get_db())
    if request.method == "POST":
        if maintenance_notice:
            flash("系统维护中，当前暂停新用户注册。", "warning")
            return redirect(url_for("register"))

        username = request.form.get("username", "").strip()
        nickname = request.form.get("nickname", "").strip() or username
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        errors = []
        if not re.fullmatch(r"[\w\u4e00-\u9fff]{2,20}", username):
            errors.append("用户名需为 2-20 位中英文、数字或下划线。")
        if not email or "@" not in email:
            errors.append("请输入有效的邮箱地址。")
        if len(password) < 6:
            errors.append("密码至少需要 6 位。")
        if password != password2:
            errors.append("两次输入的密码不一致。")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("register.html")

        db = get_db()
        try:
            db.execute(
                """
                INSERT INTO users (username, email, password_hash, nickname, role, status)
                VALUES (?, ?, ?, ?, 'user', 'active')
                """,
                (username, email, generate_password_hash(password), nickname),
            )
            db.commit()
            user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            record_audit("register", "user", user_id, f"新用户注册：{username}")
            flash("注册成功，请登录。", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("用户名或邮箱已被占用。", "danger")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_page = request.args.get("next") or url_for("index")

        db = get_db()
        user = db.execute(
            """
            SELECT *
            FROM users
            WHERE username = ? OR email = ?
            """,
            (username, username.lower()),
        ).fetchone()

        if user and user["status"] == "banned":
            flash("该账号已被管理员封禁。", "danger")
            return render_template("login.html")

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            db.execute(
                "UPDATE users SET last_login_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user["id"],),
            )
            db.commit()
            record_audit("login", "user", user["id"], f"登录账号：{user['username']}")
            flash("登录成功。", "success")
            return redirect(next_page)

        flash("用户名或密码错误。", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    if current_user():
        record_audit("logout", "user", current_user()["id"], f"退出账号：{current_user()['username']}")
    session.pop("user_id", None)
    flash("你已退出登录。", "info")
    return redirect(url_for("index"))


@app.route("/profile/<username>")
def profile(username):
    db = get_db()
    profile_user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if profile_user is None:
        flash("用户不存在。", "danger")
        return redirect(url_for("index"))

    can_view_all = current_user() and (
        current_user()["username"] == username or is_admin(current_user())
    )
    if can_view_all:
        article_clause = "a.author_id = ?"
        params = (profile_user["id"],)
    else:
        article_clause = "a.author_id = ? AND a.status = 'published' AND COALESCE(a.is_hidden, 0) = 0"
        params = (profile_user["id"],)

    articles = db.execute(
        f"""
        SELECT a.*, c.name AS category_name, c.slug AS category_slug
        FROM articles a
        LEFT JOIN categories c ON c.id = a.category_id
        WHERE {article_clause}
        ORDER BY a.created_at DESC
        LIMIT 10
        """,
        params,
    ).fetchall()

    article_count = db.execute(
        """
        SELECT COUNT(*)
        FROM articles
        WHERE author_id = ?
        """,
        (profile_user["id"],),
    ).fetchone()[0]
    comment_count = db.execute(
        "SELECT COUNT(*) FROM comments WHERE user_id = ?",
        (profile_user["id"],),
    ).fetchone()[0]

    return render_template(
        "profile.html",
        profile_user=profile_user,
        articles=enrich_articles(db, articles),
        article_count=article_count,
        comment_count=comment_count,
        can_view_all=can_view_all,
    )


@app.route("/me/articles")
@login_required
def my_articles():
    db = get_db()
    articles = db.execute(
        """
        SELECT a.*, c.name AS category_name, c.slug AS category_slug,
               (
                   SELECT COUNT(*) FROM comments co
                   WHERE co.article_id = a.id AND COALESCE(co.is_hidden, 0) = 0
               ) AS comment_count,
               (
                   SELECT COUNT(*) FROM likes l WHERE l.article_id = a.id
               ) AS like_count
        FROM articles a
        LEFT JOIN categories c ON c.id = a.category_id
        WHERE a.author_id = ?
        ORDER BY a.updated_at DESC, a.created_at DESC
        """,
        (current_user()["id"],),
    ).fetchall()
    return render_template("my_articles.html", articles=enrich_articles(db, articles))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (current_user()["id"],)).fetchone()

    if request.method == "POST":
        blocked = ensure_write_allowed()
        if blocked:
            return blocked

        nickname = request.form.get("nickname", "").strip()
        email = request.form.get("email", "").strip().lower()
        bio = request.form.get("bio", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if not email or "@" not in email:
            errors.append("请输入有效的邮箱地址。")
        if new_password or confirm_password or current_password:
            if not check_password_hash(user["password_hash"], current_password):
                errors.append("原密码输入错误。")
            if len(new_password) < 6:
                errors.append("新密码至少需要 6 位。")
            if new_password != confirm_password:
                errors.append("两次输入的新密码不一致。")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("settings.html", user=user)

        db.execute(
            """
            UPDATE users
            SET nickname = ?, email = ?, bio = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (nickname, email, bio, current_user()["id"]),
        )
        record_detail = "更新个人资料"
        if new_password:
            db.execute(
                "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (generate_password_hash(new_password), current_user()["id"]),
            )
            record_detail = "更新个人资料并修改密码"
        db.commit()
        record_audit("update_profile", "user", current_user()["id"], record_detail)
        flash("账户设置已保存。", "success")
        return redirect(url_for("settings"))

    return render_template("settings.html", user=user)


def get_article_for_edit(article_id):
    db = get_db()
    article = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if article is None:
        flash("文章不存在。", "danger")
        return None
    if article["author_id"] != current_user()["id"] and not is_admin():
        flash("你没有权限编辑这篇文章。", "danger")
        return None
    return article


def validate_article_form(form):
    title = form.get("title", "").strip()
    content = form.get("content", "").strip()
    excerpt = form.get("excerpt", "").strip()
    category_id = form.get("category_id", type=int)
    status = form.get("status", "published")
    tags = [tag.strip() for tag in form.get("tags", "").split(",") if tag.strip()]

    errors = []
    if len(title) < 4 or len(title) > 80:
        errors.append("文章标题需在 4 到 80 个字符之间。")
    if len(content) < 20:
        errors.append("文章内容至少需要 20 个字符。")
    if status not in {"draft", "published"}:
        errors.append("文章状态不合法。")
    if not excerpt:
        excerpt = build_excerpt(content)
    return {
        "title": title,
        "content": content,
        "excerpt": excerpt,
        "category_id": category_id,
        "status": status,
        "tags": tags,
        "errors": errors,
    }


@app.route("/create", methods=["GET", "POST"])
@login_required
def create_article():
    db = get_db()
    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    all_tags = db.execute("SELECT * FROM tags ORDER BY name").fetchall()

    if request.method == "POST":
        blocked = ensure_write_allowed()
        if blocked:
            return blocked

        payload = validate_article_form(request.form)
        if payload["errors"]:
            for error in payload["errors"]:
                flash(error, "danger")
            return render_template(
                "create_article.html",
                categories=categories,
                all_tags=all_tags,
                article=request.form,
            )

        base_slug = generate_slug(payload["title"])
        slug = base_slug
        index = 1
        while db.execute("SELECT 1 FROM articles WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base_slug}-{index}"
            index += 1

        db.execute(
            """
            INSERT INTO articles (title, slug, content, excerpt, author_id, category_id, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                payload["title"],
                slug,
                payload["content"],
                payload["excerpt"],
                current_user()["id"],
                payload["category_id"],
                payload["status"],
            ),
        )
        article_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        update_article_tags(db, article_id, payload["tags"])
        db.commit()
        record_audit("create_article", "article", article_id, f"创建文章：{payload['title']}")
        flash("文章已保存。", "success")
        if payload["status"] == "draft":
            return redirect(url_for("my_articles"))
        return redirect(url_for("article_detail", slug=slug))

    return render_template("create_article.html", categories=categories, all_tags=all_tags, article=None)


@app.route("/edit/<int:article_id>", methods=["GET", "POST"])
@login_required
def edit_article(article_id):
    db = get_db()
    article = get_article_for_edit(article_id)
    if article is None:
        return redirect(url_for("my_articles"))

    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    all_tags = db.execute("SELECT * FROM tags ORDER BY name").fetchall()
    article_tags = get_article_tags(db, article_id)

    if request.method == "POST":
        blocked = ensure_write_allowed()
        if blocked:
            return blocked

        payload = validate_article_form(request.form)
        if payload["errors"]:
            for error in payload["errors"]:
                flash(error, "danger")
            article_view = dict(article)
            article_view.update(request.form)
            return render_template(
                "create_article.html",
                article=article_view,
                article_tags=article_tags,
                categories=categories,
                all_tags=all_tags,
            )

        db.execute(
            """
            UPDATE articles
            SET title = ?, content = ?, excerpt = ?, category_id = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                payload["title"],
                payload["content"],
                payload["excerpt"],
                payload["category_id"],
                payload["status"],
                article_id,
            ),
        )
        update_article_tags(db, article_id, payload["tags"])
        db.commit()
        record_audit("edit_article", "article", article_id, f"编辑文章：{payload['title']}")
        flash("文章已更新。", "success")
        if payload["status"] == "draft":
            return redirect(url_for("my_articles"))
        return redirect(url_for("article_detail", slug=article["slug"]))

    return render_template(
        "create_article.html",
        article=article,
        article_tags=article_tags,
        categories=categories,
        all_tags=all_tags,
    )


@app.route("/delete/<int:article_id>", methods=["POST"])
@login_required
def delete_article(article_id):
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    db = get_db()
    article = get_article_for_edit(article_id)
    if article is None:
        return redirect(url_for("my_articles"))

    db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    db.commit()
    record_audit("delete_article", "article", article_id, f"删除文章：{article['title']}")
    flash("文章已删除。", "success")
    return redirect(request.referrer or url_for("my_articles"))


@app.route("/preview-markdown", methods=["POST"])
@login_required
def preview_markdown():
    content = ""
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        content = payload.get("content", "")
    else:
        content = request.form.get("content", "")
    return jsonify({"html": render_markdown(content)})


@app.route("/comment/<int:article_id>", methods=["POST"])
@login_required
def add_comment(article_id):
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    db = get_db()
    article = db.execute(
        """
        SELECT *
        FROM articles
        WHERE id = ? AND status = 'published' AND COALESCE(is_hidden, 0) = 0
        """,
        (article_id,),
    ).fetchone()
    if article is None:
        flash("文章当前不可评论。", "warning")
        return redirect(url_for("index"))

    content = request.form.get("content", "").strip()
    parent_id = request.form.get("parent_id", type=int)
    if len(content) < 2:
        flash("评论内容不能少于 2 个字符。", "danger")
        return redirect(url_for("article_detail", slug=article["slug"]))

    if parent_id:
        parent = db.execute(
            """
            SELECT * FROM comments
            WHERE id = ? AND article_id = ? AND COALESCE(is_hidden, 0) = 0
            """,
            (parent_id, article_id),
        ).fetchone()
        if parent is None:
            flash("回复目标不存在。", "danger")
            return redirect(url_for("article_detail", slug=article["slug"]))

    db.execute(
        """
        INSERT INTO comments (article_id, user_id, parent_id, content)
        VALUES (?, ?, ?, ?)
        """,
        (article_id, current_user()["id"], parent_id, content),
    )
    db.commit()
    comment_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    record_audit("create_comment", "comment", comment_id, f"评论文章 ID={article_id}")
    flash("评论发布成功。", "success")
    return redirect(url_for("article_detail", slug=article["slug"]))


@app.route("/comment/delete/<int:comment_id>", methods=["POST"])
@login_required
def delete_comment(comment_id):
    blocked = ensure_write_allowed()
    if blocked:
        return blocked

    db = get_db()
    comment = db.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if comment is None:
        flash("评论不存在。", "danger")
        return redirect(request.referrer or url_for("index"))

    owns_comment = comment["user_id"] == current_user()["id"]
    if not owns_comment and not is_admin():
        flash("你没有权限删除这条评论。", "danger")
        return redirect(request.referrer or url_for("index"))

    db.execute("DELETE FROM comments WHERE id = ? OR parent_id = ?", (comment_id, comment_id))
    db.commit()
    record_audit("delete_comment", "comment", comment_id, "删除评论及其回复")
    flash("评论已删除。", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/like/<int:article_id>", methods=["POST"])
@login_required
def like_article(article_id):
    blocked = ensure_write_allowed()
    if blocked:
        return jsonify({"error": "write blocked"}), 403

    db = get_db()
    article = db.execute(
        """
        SELECT *
        FROM articles
        WHERE id = ? AND status = 'published' AND COALESCE(is_hidden, 0) = 0
        """,
        (article_id,),
    ).fetchone()
    if article is None:
        return jsonify({"error": "article unavailable"}), 404

    existing = db.execute(
        "SELECT 1 FROM likes WHERE user_id = ? AND article_id = ?",
        (current_user()["id"], article_id),
    ).fetchone()
    if existing:
        db.execute(
            "DELETE FROM likes WHERE user_id = ? AND article_id = ?",
            (current_user()["id"], article_id),
        )
        liked = False
        action = "取消点赞"
    else:
        db.execute(
            "INSERT INTO likes (user_id, article_id) VALUES (?, ?)",
            (current_user()["id"], article_id),
        )
        liked = True
        action = "点赞文章"
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM likes WHERE article_id = ?", (article_id,)).fetchone()[0]
    record_audit("like_article", "article", article_id, action)
    return jsonify({"liked": liked, "count": count})


@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    stats = {
        "user_count": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "banned_user_count": db.execute(
            "SELECT COUNT(*) FROM users WHERE status = 'banned'"
        ).fetchone()[0],
        "article_count": db.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
        "hidden_article_count": db.execute(
            "SELECT COUNT(*) FROM articles WHERE COALESCE(is_hidden, 0) = 1"
        ).fetchone()[0],
        "draft_count": db.execute("SELECT COUNT(*) FROM articles WHERE status = 'draft'").fetchone()[0],
        "comment_count": db.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
        "hidden_comment_count": db.execute(
            "SELECT COUNT(*) FROM comments WHERE COALESCE(is_hidden, 0) = 1"
        ).fetchone()[0],
        "announcement_count": db.execute("SELECT COUNT(*) FROM announcements").fetchone()[0],
        "total_views": db.execute("SELECT COALESCE(SUM(view_count), 0) FROM articles").fetchone()[0],
    }
    trends = {
        "new_users": db.execute(
            "SELECT COUNT(*) FROM users WHERE datetime(created_at) >= datetime('now', '-7 day')"
        ).fetchone()[0],
        "new_articles": db.execute(
            "SELECT COUNT(*) FROM articles WHERE datetime(created_at) >= datetime('now', '-7 day')"
        ).fetchone()[0],
        "new_comments": db.execute(
            "SELECT COUNT(*) FROM comments WHERE datetime(created_at) >= datetime('now', '-7 day')"
        ).fetchone()[0],
        "new_logs": db.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE datetime(created_at) >= datetime('now', '-7 day')"
        ).fetchone()[0],
    }
    recent_articles = db.execute(
        """
        SELECT a.*, u.username
        FROM articles a
        JOIN users u ON u.id = a.author_id
        ORDER BY a.created_at DESC
        LIMIT 6
        """
    ).fetchall()
    recent_logs = db.execute(
        """
        SELECT l.*, u.username
        FROM audit_logs l
        LEFT JOIN users u ON u.id = l.actor_id
        ORDER BY l.created_at DESC
        LIMIT 8
        """
    ).fetchall()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        trends=trends,
        recent_articles=enrich_articles(db, recent_articles),
        recent_logs=recent_logs,
    )


@app.route("/admin/articles")
@admin_required
def admin_articles():
    db = get_db()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * app.config["PER_PAGE"]
    total = db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    articles = db.execute(
        """
        SELECT a.*, u.username, c.name AS category_name
        FROM articles a
        JOIN users u ON u.id = a.author_id
        LEFT JOIN categories c ON c.id = a.category_id
        ORDER BY a.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (app.config["PER_PAGE"], offset),
    ).fetchall()
    total_pages = max(1, (total + app.config["PER_PAGE"] - 1) // app.config["PER_PAGE"])
    return render_template(
        "admin/articles.html",
        articles=enrich_articles(db, articles),
        page=page,
        total_pages=total_pages,
    )


@app.route("/admin/articles/<int:article_id>/action", methods=["POST"])
@admin_required
def admin_article_action(article_id):
    db = get_db()
    article = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if article is None:
        flash("文章不存在。", "danger")
        return redirect(url_for("admin_articles"))

    action = request.form.get("action")
    if action == "toggle_top":
        db.execute("UPDATE articles SET is_top = CASE WHEN is_top = 1 THEN 0 ELSE 1 END WHERE id = ?", (article_id,))
        detail = "切换文章置顶状态"
    elif action == "toggle_hidden":
        db.execute(
            "UPDATE articles SET is_hidden = CASE WHEN COALESCE(is_hidden, 0) = 1 THEN 0 ELSE 1 END, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (article_id,),
        )
        detail = "切换文章隐藏状态"
    elif action == "publish":
        db.execute(
            "UPDATE articles SET status = 'published', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (article_id,),
        )
        detail = "发布文章"
    elif action == "draft":
        db.execute(
            "UPDATE articles SET status = 'draft', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (article_id,),
        )
        detail = "转为草稿"
    elif action == "delete":
        db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        detail = "永久删除文章"
    else:
        flash("未知操作。", "danger")
        return redirect(url_for("admin_articles"))

    db.commit()
    record_audit("admin_article_action", "article", article_id, f"{detail}：{article['title']}")
    flash("文章状态已更新。", "success")
    return redirect(url_for("admin_articles"))


@app.route("/admin/users")
@admin_required
def admin_users():
    db = get_db()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * app.config["PER_PAGE"]
    total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    users = db.execute(
        """
        SELECT u.*,
               (SELECT COUNT(*) FROM articles WHERE author_id = u.id) AS article_count,
               (SELECT COUNT(*) FROM comments WHERE user_id = u.id) AS comment_count
        FROM users u
        ORDER BY u.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (app.config["PER_PAGE"], offset),
    ).fetchall()
    total_pages = max(1, (total + app.config["PER_PAGE"] - 1) // app.config["PER_PAGE"])
    return render_template("admin/users.html", users=users, page=page, total_pages=total_pages)


@app.route("/admin/users/<int:user_id>/action", methods=["POST"])
@admin_required
def admin_user_action(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        flash("用户不存在。", "danger")
        return redirect(url_for("admin_users"))
    if user["id"] == current_user()["id"]:
        flash("不能对当前管理员自己执行该操作。", "warning")
        return redirect(url_for("admin_users"))

    action = request.form.get("action")
    if action == "toggle_ban":
        new_status = "active" if user["status"] == "banned" else "banned"
        db.execute("UPDATE users SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_status, user_id))
        detail = "封禁用户" if new_status == "banned" else "解封用户"
    elif action == "toggle_role":
        new_role = "user" if user["role"] == "admin" else "admin"
        db.execute("UPDATE users SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_role, user_id))
        detail = "切换用户角色"
    else:
        flash("未知操作。", "danger")
        return redirect(url_for("admin_users"))

    db.commit()
    record_audit("admin_user_action", "user", user_id, f"{detail}：{user['username']}")
    flash("用户状态已更新。", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/comments")
@admin_required
def admin_comments():
    db = get_db()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * app.config["PER_PAGE"]
    total = db.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    comments = db.execute(
        """
        SELECT c.*, u.username, a.title AS article_title, a.slug AS article_slug
        FROM comments c
        JOIN users u ON u.id = c.user_id
        JOIN articles a ON a.id = c.article_id
        ORDER BY c.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (app.config["PER_PAGE"], offset),
    ).fetchall()
    total_pages = max(1, (total + app.config["PER_PAGE"] - 1) // app.config["PER_PAGE"])
    return render_template(
        "admin/comments.html",
        comments=comments,
        page=page,
        total_pages=total_pages,
    )


@app.route("/admin/comments/<int:comment_id>/action", methods=["POST"])
@admin_required
def admin_comment_action(comment_id):
    db = get_db()
    comment = db.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if comment is None:
        flash("评论不存在。", "danger")
        return redirect(url_for("admin_comments"))

    action = request.form.get("action")
    if action == "toggle_hidden":
        db.execute(
            "UPDATE comments SET is_hidden = CASE WHEN COALESCE(is_hidden, 0) = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (comment_id,),
        )
        detail = "切换评论隐藏状态"
    elif action == "delete":
        db.execute("DELETE FROM comments WHERE id = ? OR parent_id = ?", (comment_id, comment_id))
        detail = "删除评论"
    else:
        flash("未知操作。", "danger")
        return redirect(url_for("admin_comments"))

    db.commit()
    record_audit("admin_comment_action", "comment", comment_id, detail)
    flash("评论状态已更新。", "success")
    return redirect(url_for("admin_comments"))


@app.route("/admin/categories", methods=["GET", "POST"])
@admin_required
def admin_categories():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if len(name) < 2:
            flash("分类名称至少 2 个字符。", "danger")
            return redirect(url_for("admin_categories"))
        slug = generate_slug(name)
        try:
            db.execute(
                "INSERT INTO categories (name, slug, description) VALUES (?, ?, ?)",
                (name, slug, description),
            )
            db.commit()
            category_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            record_audit("create_category", "category", category_id, f"创建分类：{name}")
            flash("分类创建成功。", "success")
        except sqlite3.IntegrityError:
            flash("分类名称已存在。", "danger")
        return redirect(url_for("admin_categories"))

    categories = db.execute(
        """
        SELECT c.*,
               (
                   SELECT COUNT(*)
                   FROM articles a
                   WHERE a.category_id = c.id
               ) AS article_count
        FROM categories c
        ORDER BY c.name
        """
    ).fetchall()
    return render_template("admin/categories.html", categories=categories)


@app.route("/admin/categories/delete/<int:cat_id>", methods=["POST"])
@admin_required
def delete_category(cat_id):
    db = get_db()
    category = db.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
    fallback = db.execute("SELECT id FROM categories WHERE slug = 'uncategorized'").fetchone()
    if category is None or fallback is None:
        flash("分类不存在。", "danger")
        return redirect(url_for("admin_categories"))
    if category["slug"] == "uncategorized":
        flash("默认分类不能删除。", "warning")
        return redirect(url_for("admin_categories"))

    db.execute("UPDATE articles SET category_id = ? WHERE category_id = ?", (fallback["id"], cat_id))
    db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    db.commit()
    record_audit("delete_category", "category", cat_id, f"删除分类：{category['name']}")
    flash("分类已删除，相关文章已转入“未分类”。", "success")
    return redirect(url_for("admin_categories"))


@app.route("/admin/announcements", methods=["GET", "POST"])
@admin_required
def admin_announcements():
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        kind = request.form.get("kind", "notice")
        is_active = 1 if request.form.get("is_active") == "1" else 0
        if len(title) < 4 or len(content) < 6:
            flash("公告标题和内容不能过短。", "danger")
            return redirect(url_for("admin_announcements"))
        if kind not in {"notice", "maintenance"}:
            flash("公告类型不合法。", "danger")
            return redirect(url_for("admin_announcements"))
        db.execute(
            """
            INSERT INTO announcements (title, content, kind, is_active, created_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, content, kind, is_active, current_user()["id"]),
        )
        db.commit()
        announcement_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        record_audit("create_announcement", "announcement", announcement_id, f"发布公告：{title}")
        flash("公告已创建。", "success")
        return redirect(url_for("admin_announcements"))

    announcements = db.execute(
        """
        SELECT an.*, u.username
        FROM announcements an
        LEFT JOIN users u ON u.id = an.created_by
        ORDER BY an.created_at DESC
        """
    ).fetchall()
    return render_template("admin/announcements.html", announcements=announcements)


@app.route("/admin/announcements/<int:announcement_id>/action", methods=["POST"])
@admin_required
def admin_announcement_action(announcement_id):
    db = get_db()
    announcement = db.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,)).fetchone()
    if announcement is None:
        flash("公告不存在。", "danger")
        return redirect(url_for("admin_announcements"))

    action = request.form.get("action")
    if action == "toggle_active":
        db.execute(
            "UPDATE announcements SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (announcement_id,),
        )
        detail = "切换公告启用状态"
    elif action == "delete":
        db.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
        detail = "删除公告"
    else:
        flash("未知操作。", "danger")
        return redirect(url_for("admin_announcements"))

    db.commit()
    record_audit("announcement_action", "announcement", announcement_id, f"{detail}：{announcement['title']}")
    flash("公告状态已更新。", "success")
    return redirect(url_for("admin_announcements"))


@app.route("/admin/logs")
@admin_required
def admin_logs():
    db = get_db()
    page = request.args.get("page", 1, type=int)
    offset = (page - 1) * app.config["PER_PAGE"]
    total = db.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    logs = db.execute(
        """
        SELECT l.*, u.username
        FROM audit_logs l
        LEFT JOIN users u ON u.id = l.actor_id
        ORDER BY l.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (app.config["PER_PAGE"], offset),
    ).fetchall()
    total_pages = max(1, (total + app.config["PER_PAGE"] - 1) // app.config["PER_PAGE"])
    return render_template("admin/logs.html", logs=logs, page=page, total_pages=total_pages)


@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("Database initialized.")


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
