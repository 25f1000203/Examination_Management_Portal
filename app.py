
from flask import Flask, render_template, session

import config
import database
from helpers import check_csrf, csrf_token
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.examiner import examiner_bp
from routes.student import student_bp


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY

    database.init_db()                          # creates tables + Admin the first time
    app.teardown_appcontext(database.close_db)  # close the DB connection after each request

    for blueprint in (auth_bp, admin_bp, examiner_bp, student_bp):
        app.register_blueprint(blueprint)

    app.before_request(check_csrf)              # every POST form must carry the CSRF token
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.filters["dt"] = lambda text: (text or "-").replace("T", " ")
    app.jinja_env.filters["num"] = lambda v: "-" if v is None else f"{v:g}"

    @app.context_processor
    def inject_user():
        return {"role": session.get("role"), "user_name": session.get("name")}

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, message="You do not have permission to open this page."), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="The page you asked for does not exist."), 404

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message="Bad request (the form may have expired). Go back and try again."), 400

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
