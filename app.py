import os, uuid
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "change-me-in-production"),
    SQLALCHEMY_DATABASE_URI="sqlite:///arctin.db",
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
)
UPLOADS = os.path.join(app.static_folder, "uploads")
os.makedirs(UPLOADS, exist_ok=True)
ALLOWED = {"png", "jpg", "jpeg", "gif", "webp"}
GENRES = ["Device", "Electronics", "Micro", "Strategy", "RPG"]

db = SQLAlchemy(app)
login = LoginManager(app)
login.login_view = "auth"

item_tags = db.Table("item_tags",
    db.Column("item_id", db.Integer, db.ForeignKey("item.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tag.id"), primary_key=True))
wishlist_table = db.Table("wishlist",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("item_id", db.Integer, db.ForeignKey("item.id"), primary_key=True))


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(40), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    avatar = db.Column(db.String(120))
    is_admin = db.Column(db.Boolean, default=False)
    wishlist = db.relationship("Item", secondary=wishlist_table)


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), unique=True, nullable=False)


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Float, nullable=False)
    photo = db.Column(db.String(120))
    genre = db.Column(db.String(30))
    created = db.Column(db.DateTime, server_default=db.func.now())
    tags = db.relationship("Tag", secondary=item_tags, backref="items")


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    qty = db.Column(db.Integer, default=1)
    item = db.relationship("Item")


@login.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


@app.before_request
def require_login():
    if request.endpoint in ("auth", "static") or current_user.is_authenticated:
        return
    return redirect(url_for("auth"))


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(404)  # hide the dashboard's existence from customers
        return f(*a, **kw)
    return wrapper


def save_image(file):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED:
        return None
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOADS, name))
    return name


@app.route("/auth", methods=["GET", "POST"])
def auth():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        pw = request.form["password"]
        if request.form["action"] == "register":
            username = request.form.get("username", "").strip()
            if not (email and username and len(pw) >= 8):
                flash("Enter an email, a username, and a password of 8+ characters.")
            elif User.query.filter((User.email == email) | (User.username == username)).first():
                flash("That email or username is already taken.")
            else:
                u = User(email=email, username=username, password_hash=generate_password_hash(pw))
                db.session.add(u); db.session.commit()
                login_user(u)
                return redirect(url_for("home"))
        else:
            u = User.query.filter_by(email=email).first()
            if u and check_password_hash(u.password_hash, pw):
                login_user(u)
                return redirect(url_for("home"))
            flash("Wrong email or password.")
    return render_template("auth.html")


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("auth"))


@app.route("/")
def home():
    items = Item.query.order_by(Item.created.desc()).all()
    return render_template("home.html", items=items)


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    genre = request.args.get("genre", "")
    tag = request.args.get("tag", "").strip()
    query = Item.query
    if q:
        like = f"%{q}%"
        query = query.filter(Item.title.ilike(like) | Item.description.ilike(like))
    if genre:
        query = query.filter(Item.genre == genre)
    if tag:
        query = query.filter(Item.tags.any(Tag.name.ilike(tag)))
    searched = bool(q or genre or tag)
    return render_template("search.html", items=query.all() if searched else [],
                           q=q, genre=genre, tag=tag, genres=GENRES,
                           searched=searched, all_tags=Tag.query.order_by(Tag.name).all())


@app.route("/wishlist/<int:item_id>", methods=["POST"])
def wishlist(item_id):
    item = db.get_or_404(Item, item_id)
    if item in current_user.wishlist:
        current_user.wishlist.remove(item)
    else:
        current_user.wishlist.append(item)
    db.session.commit()
    return redirect(request.referrer or url_for("home"))


@app.route("/cart")
def cart():
    rows = CartItem.query.filter_by(user_id=current_user.id).all()
    total = sum(r.item.price * r.qty for r in rows)
    return render_template("cart.html", rows=rows, total=total)


@app.route("/cart/add/<int:item_id>", methods=["POST"])
def cart_add(item_id):
    db.get_or_404(Item, item_id)
    row = CartItem.query.filter_by(user_id=current_user.id, item_id=item_id).first()
    if row:
        row.qty += 1
    else:
        db.session.add(CartItem(user_id=current_user.id, item_id=item_id))
    db.session.commit()
    flash("Added to your cart.")
    return redirect(request.referrer or url_for("home"))


@app.route("/cart/remove/<int:row_id>", methods=["POST"])
def cart_remove(row_id):
    row = db.get_or_404(CartItem, row_id)
    if row.user_id == current_user.id:
        db.session.delete(row); db.session.commit()
    return redirect(url_for("cart"))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        name = request.form["username"].strip()
        taken = User.query.filter(User.username == name, User.id != current_user.id).first()
        if not name:
            flash("Username can't be empty.")
        elif taken:
            flash("That username is taken.")
        else:
            current_user.username = name
            avatar = save_image(request.files.get("avatar"))
            if avatar:
                current_user.avatar = avatar
            db.session.commit()
            flash("Profile saved.")
        return redirect(url_for("profile"))
    return render_template("profile.html")


@app.route("/admin", methods=["GET", "POST"])
@login_required
@admin_required
def admin():
    if request.method == "POST":
        try:
            price = float(request.form["price"])
        except ValueError:
            flash("Price must be a number.")
            return redirect(url_for("admin"))
        item = Item(title=request.form["title"].strip(),
                    description=request.form["description"].strip(),
                    price=price, genre=request.form.get("genre") or None,
                    photo=save_image(request.files.get("photo")))
        for name in {t.strip() for t in request.form.get("tags", "").split(",") if t.strip()}:
            tag = Tag.query.filter(db.func.lower(Tag.name) == name.lower()).first() or Tag(name=name)
            item.tags.append(tag)
        db.session.add(item); db.session.commit()
        flash("Item published.")
        return redirect(url_for("admin"))
    return render_template("admin.html", items=Item.query.order_by(Item.created.desc()).all(), genres=GENRES)


@app.route("/admin/delete/<int:item_id>", methods=["POST"])
@login_required
@admin_required
def admin_delete(item_id):
    item = db.get_or_404(Item, item_id)
    CartItem.query.filter_by(item_id=item.id).delete()
    db.session.delete(item); db.session.commit()
    flash("Item deleted.")
    return redirect(url_for("admin"))


def seed_admin():
    email = os.environ.get("ADMIN_EMAIL", "admin@arctin.gg")
    if not User.query.filter_by(is_admin=True).first():
        pw = os.environ.get("ADMIN_PASSWORD", "changeme123")
        db.session.add(User(email=email, username="admin", is_admin=True,
                            password_hash=generate_password_hash(pw)))
        db.session.commit()
        print(f"Created admin account {email} - change its password before going live.")


with app.app_context():
    db.create_all()
    seed_admin()

if __name__ == "__main__":
    app.run(debug=False)
