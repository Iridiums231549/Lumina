# Lumina Blog System

Lumina 是一个基于 Flask 的博客网站系统，支持：

- 用户注册、登录、资料维护、修改密码
- Markdown 文章创作、草稿管理、分类与标签
- 评论回复、点赞互动、我的文章
- 管理员后台、公告中心、用户治理、审计日志

## Local Run

```bash
python -m pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Default Accounts

- Admin: `admin / admin123`
- Demo user: `editor / editor123`

## Deployment

This project includes:

- `render.yaml` for Render deployment
- `部署说明.txt` for Chinese deployment notes
- `启动公网分享.bat` for temporary public sharing with Cloudflare Tunnel

For a long-term public deployment, use Render with the included persistent disk
configuration so the SQLite database is not lost after restarts or redeploys.

## Important Note

GitHub is used for source hosting and version control.
For a permanent public website, deploy this Flask app to a Python hosting platform such as Render.
