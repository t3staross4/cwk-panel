import hmac
import hashlib
import base64
import gzip
import hashlib
from io import BytesIO
import json
import random
import re
import shutil
import secrets
import string
import subprocess
import schedule
import threading
import time
from flask import Flask, Request, render_template, make_response, jsonify, request, redirect, abort, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
import os
import argparse
from urllib.parse import parse_qs
from datetime import datetime, timedelta, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


#check if flaskkey exists
if not os.path.exists("flaskkey"):
	print("Creating new Flask secret key")
	#create a new key
	with open("flaskkey", "w") as f:
		f.write(''.join(random.choice(string.ascii_letters + string.digits) for i in range(50)))
app.secret_key = open("flaskkey", "r").read()

bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = '/'

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=5000)
parser.add_argument('--debug', action='store_true')

args, _ = parser.parse_known_args()

app = Flask(__name__)

class Base(DeclarativeBase):
  pass
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///cardwarskingdom.db"
db = SQLAlchemy(model_class=Base)
db.init_app(app)

badcharaters = ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ";", "%", "^", "&", "(", ")", "{", "}", "[", "]", ".", ",", "'", "`", "!", "$", "#", "@", "+", "="]

maintenance = False

@app.route("/static/version.txt")
def PersistVersion():
    #create version.txt and android_version.txt if they don't exist
	if not os.path.exists("data/persist/version.txt"):
		with open("data/persist/version.txt", "w") as f:
			f.write("1.0.0")
	if not os.path.exists("data/persist/android_version.txt"):
		with open("data/persist/android_version.txt", "w") as f:
			f.write("1.0.0")
    
	with open("data/persist/version.txt", "r") as f:
		pc_version = f.read()
	with open("data/persist/android_version.txt", "r") as f:
		android_version = f.read()
  
	data = {
		"maintenance_mode": "yes" if maintenance else "no",
		"message": "Card Wars Kingdom is currently undergoing maintenance.\n\nPlease try again later.",
		"icon": "",
		"clickable": "yes",
		"android_version": android_version,
		"version": pc_version,
		"android_url": "",
		"pc_url": "",
	}
	return json.dumps(data)

class AdminActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    time = db.Column(db.Integer, nullable=False, default=int(time.time()))
    message = db.Column(db.String(8192), nullable=True)

class Admin(UserMixin, db.Model):
	username: Mapped[str] = mapped_column(db.String(80), primary_key=True, unique=True, nullable=False)
	password: Mapped[str] = mapped_column(db.String(80), nullable=False)
	rank: Mapped[int] = mapped_column(db.Integer, nullable=False)

	def get_id(self):
		return str(self.username)
	
@login_manager.user_loader
def load_user(user_id):
	return Admin.query.get(user_id)

@app.route("/admin", methods=['GET', 'POST'])
def AdminPage():

    #create an admin account if one doesn't exist
	if not Admin.query.first():
		randompassword = ''.join(random.choices(string.ascii_letters + string.digits, k=24))
		newAdmin = Admin(username="admin", password=bcrypt.generate_password_hash(randompassword).decode('utf-8'), rank=0)
		db.session.add(newAdmin)
		db.session.commit()
		Log("server", "Created admin account")
		print(f"Admin account created! Username: admin, Password: {randompassword}")

	if request.method == 'GET':
		if current_user.is_authenticated:
			if not isAdmin(current_user):
				return abort(404)
			return redirect("/admin/home")
		else:
			return render_template('admin_login.html')
	if request.method == 'POST':
		username = request.form['username']
		password = request.form['password']
		username = re.sub(r'[^a-zA-Z0-9]', '', username)
		db_user = Admin.query.filter_by(username=username).first()
		if db_user is None:
			return make_response("Invalid Username or Password!", 400)
		if not bcrypt.check_password_hash(db_user.password, password):
			return make_response("Invalid Password or Username!", 400)
		login_user(db_user, remember=True)
		return redirect("/admin")

def isAdmin(user):
	if not user.is_authenticated:
		return False
	db_user = Admin.query.filter_by(username=user.username).first()
	return db_user is not None

@login_required
@app.route("/admin/home")
def AdminHome():
	if not isAdmin(current_user):
		return abort(404)

	adminActivity = AdminActivity.query.order_by(AdminActivity.time).all()
	#convert time
	for log in adminActivity:
		log.time = datetime.fromtimestamp(log.time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

	#reverse list
	adminActivity.reverse()

	return render_template('admin_home.html', Activity=adminActivity)

@login_required
@app.route("/admin/versions" , methods=['GET', 'POST'])
def AdminVersions():
	if not isAdmin(current_user):
		return abort(404)

	if request.method == 'GET':

		return render_template('admin_versions.html' , pc_version=open("data/persist/version.txt", "r").read(), android_version=open("data/persist/android_version.txt", "r").read())
	elif request.method == 'POST':
		form = request.form
		form = {k: v[0] if len(v) == 1 else v for k, v in form.items()}

		if "pc_version" not in form or form["pc_version"] == "":
			return make_response("Invalid PC version!", 400)
		if "android_version" not in form or form["android_version"] == "":
			return make_response("Invalid Android version!", 400)

		return redirect("/admin/versions")

@login_required
@app.route("/admin/server")
def AdminServer():
	if not isAdmin(current_user):
		return abort(404)

	#create backup folder if it doesn't exist
	if not os.path.exists("backup"):
		os.makedirs("backup")

	#get last backup in folder
	last_backup_time = 0
	last_backup_file = ""
	for file in os.listdir("backup"):
		if file.endswith(".zip"):
			file_time = int(file.replace(".zip", "").replace("-", "").replace("_", ""))
			if file_time > last_backup_time:
				last_backup_time = file_time
				last_backup_file = file.replace(".zip", "")

	if last_backup_file == "":
		last_backup = "Never"
	else:
		last_backup = time_ago_string(datetime.strptime(last_backup_file, "%Y-%m-%d_%H-%M-%S"))

	return render_template('admin_server.html', last_backup=last_backup)

def time_ago_string(date_time):
    now = datetime.now()
    time_difference = now - date_time

    # Extracting hours and minutes
    hours = time_difference.seconds // 3600
    minutes = (time_difference.seconds // 60) % 60

    if time_difference.days > 0:
        return f"{time_difference.days} days ago"
    elif hours > 0:
        return f"{hours} {'hour' if hours == 1 else 'hours'} ago"
    elif minutes > 0:
        return f"{minutes} {'minute' if minutes == 1 else 'minutes'} ago"
    else:
        return f"{time_difference.seconds} seconds ago"

@login_required
@app.route("/admin/server/backup")
def AdminBackup():
	if not isAdmin(current_user):
		return abort(404)

	backup = Backup()
	if not backup:
		return make_response("Failed to backup", 400)
	return redirect("/admin/server")

@login_required
@app.route("/admin/server/pull")
def AdminGitPull():
	if not isAdmin(current_user):
		return abort(404)

	Log("admin", current_user.username + " pulled from git.")

	#Git pull and return response
	output = subprocess.check_output(["git", "pull"])

	Log("admin", "Pulled from git. Output: " + output.decode("utf-8"))

	#TODO: restart server

	return make_response(output.decode("utf-8"), 200)

@login_required
@app.route("/admin/createadmin", methods=['GET', 'POST'])
def AdminCreateAdmin():
	if not isAdmin(current_user):
		return abort(404)

	if request.method == 'GET':
		return render_template('admin_createadmin.html')
	if request.method == 'POST':
		username = request.form['username']
		rank = request.form['rank']

		#create random password
		password = secrets.token_urlsafe(24)
		new_admin = Admin(username=username, password=bcrypt.generate_password_hash(password).decode('utf-8'), rank=int(rank))
		db.session.add(new_admin)
		db.session.commit()

		Log("admin", current_user.username + " created admin: " + username + " with rank: " + rank)
		return "Admin created! Username: " + username + " Password: " + password

@login_required
@app.route("/admin/players")
def AdminPlayers():
	if not isAdmin(current_user):
		return abort(404)

	players = Player.query.all()

	#convert player to dict
	players = [player.as_dict() for player in players]

	#remove any players that do not have a multiplayer name
	players = [player for player in players if player["game"] != None and player["leader_level"] != None]

	#remove any player that is banned
	players = [player for player in players if not IsUserBanned(player["username"])]

	for player in players:
		player["last_online"] = datetime.fromtimestamp(player["last_online"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

		#if player's multiplayer name is empty, attempt to get it from their game
		if player["multiplayer_name"] == None:
			player["multiplayer_name"] = GetNameFromSave(player["game"])

	sortQuery = request.args.get('sort')

	if sortQuery is not None:
		players = sorted(players, key=lambda player: player[sortQuery], reverse=True)
	else:
		players = players[::-1]

	return render_template('admin_players.html', players=players, player_count=len(players))

def GetNameFromSave(save):

	try:
		game = DecryptGameData(save)
	except Exception:
		return None
	if game is None:
		return None
	return game["MultiplayerPlayerName"]

@login_required
@app.route("/admin/players/<player>")
def AdminPlayer(player):
	if not isAdmin(current_user):
		return abort(404)

	player = Player.query.filter_by(username=player).first()

	if player is None:
		return make_response("No player found!", 404)

	player = player.as_dict()

	player["last_online"] = datetime.fromtimestamp(player["last_online"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

	game = None
	try:
		game = DecryptGameData(player["game"])
		if player["multiplayer_name"] == None:
			player["multiplayer_name"] = game["MultiplayerPlayerName"]
	except Exception:
		Log("admin", "Failed to decrypt player game data for player: " + player["username"])
		game = None   

	if game is None:
		return render_template('admin_player.html', player=player) 

	battle_history = game["BattleHistory"]
	battle_history.sort(key=lambda x: x["recordTime"])

	for battle in battle_history:
		battle["recordTime"] = datetime.fromtimestamp(battle["recordTime"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

	#fix device name
	if player["devicename"] is not None:
		player["devicename"] = re.sub(r'%[0-9A-Fa-f]{2}', lambda m: chr(int(m.group(0)[1:], 16)), player["devicename"])

	Inventory = game["Inventory"]

	#remove all items that are not creatures
	if Inventory is not None:
		Inventory = [item for item in Inventory if item["_T"] == "CR"]

	return render_template('admin_player.html', player=player, is_banned=IsUserBanned(player["username"]), SoftCurrency=game["SoftCurrency"], HardCurrency=int(game["PaidHardCurrency"]) + int(game["FreeHardCurrency"]), PvpCurrency=game["PvpCurrency"], InstalledDate=datetime.fromtimestamp(game["InstalledDate"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'), PVPBanned=bool(game["Zxcvbnm"]), MultiplayerLevel=game["MultiplayerLevel"], InventorySpace=game["InventorySpace"], BattleHistory=battle_history, DeviceName=player["devicename"], Inventory=Inventory)

@login_required
@app.route("/admin/players/<player>/game")
def AdminPlayerGame(player):
	if not isAdmin(current_user):
		return abort(404)

	player = Player.query.filter_by(username=player).first()

	if player is None:
		return make_response("No player found!", 404)

	player = player.as_dict()

	game = DecryptGameData(player["game"])

	if game is None:
		return make_response("No game found!", 404)

	return jsonify(game)

def DecryptGameData(game:str):
	if game is None or game == b"" or game == b" ":
		return None
    #Decrypt
	input_data = game
	input_str = input_data.decode("utf-8")
	index = input_str.find("&data=")
	encoded_data = input_str[index + 6 :]
	array = base64.b64decode(encoded_data)
	try:
		with gzip.GzipFile(fileobj=BytesIO(array), mode='rb') as gz:
			decoded_data = gz.read().decode("utf-8")
	except Exception:
		Log("admin", "Failed to decrypt game data")
		return None

	#attempt to clean json
	decoded_data = decoded_data.replace(',}', '}').replace(',],', '],').replace(',]', ']').replace(',,', ',')

	return json.loads(decoded_data)

@login_required
@app.route("/admin/players/<player>/<action>")
def AdminPlayerAction(player, action):
	if not isAdmin(current_user):
		return abort(404)

	Log("admin", current_user.username + " performed " + action + " on " + player)

	if action == "ban":
		#check if player id is in banlist, if not, add it
		if not IsUserBanned(player):
			newban = Bans(username=player, bantype="userid", author=current_user.username, time=int(time.time()))
			db.session.add(newban)
			db.session.commit()
	elif action == "unban":
		#check if player id is in banlist, if yes, remove it
		if IsUserBanned(player):
			player_check = Bans.query.filter_by(username=player).first()
			db.session.delete(player_check)
			db.session.commit()
	else:
		return make_response("Invalid action!", 400)

	return redirect("/admin/players/" + player)

def SystemBan(username):
	Log("admin", "SYSTEM BANNED " + username)
	if not IsUserBanned(username):
		newban = Bans(username=username, bantype="userid", author="SYSTEM", time=int(time.time()))
		db.session.add(newban)
		db.session.commit()

@login_required
@app.route("/admin/ipban/<ip>/unban")
def AdminIPBan(ip):
	if not isAdmin(current_user):
		return abort(404)

	Log("admin", current_user.username + " performed unban on " + ip)

	player_check = Bans.query.filter_by(username=ip).first()
	db.session.delete(player_check)
	db.session.commit()

	return redirect("/admin/bannedips")

@login_required
@app.route("/admin/ipban", methods=['POST'])
def AdminIPBanAction():
	if not isAdmin(current_user):
		return abort(404)

	Log("admin", current_user.username + " performed ban on " + request.form['ip'])

	newban = Bans(username=request.form['ip'], bantype="ip", author=current_user.username, time=int(time.time()))
	db.session.add(newban)
	db.session.commit()

	return redirect("/admin/bannedips")


@login_required
@app.route("/admin/bannedplayers")
def AdminBannedPlayers():
	if not isAdmin(current_user):
		return abort(404)

	bans = Bans.query.filter_by(bantype="userid").all()
	bans = [ban.as_dict() for ban in bans]

	#get player name
	for ban in bans:
		ban["multiplayer_name"] = Player.query.filter_by(username=ban["username"]).first().multiplayer_name
		if ban["multiplayer_name"] is None:
			ban["multiplayer_name"] = GetNameFromSave(Player.query.filter_by(username=ban["username"]).first().game)
		if ban["time"] is not None:
			ban["time"] = datetime.fromtimestamp(ban["time"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

	return render_template('admin_bannedplayers.html', bans=bans)

@login_required
@app.route("/admin/bannedips")
def AdminBannedIPs():
	if not isAdmin(current_user):
		return abort(404)

	bans = Bans.query.filter_by(bantype="ip").all()
	bans = [ban.as_dict() for ban in bans]

	for ban in bans:
		ban["time"] = datetime.fromtimestamp(ban["time"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
	return render_template('admin_bannedips.html', bans=bans)

@login_required
@app.route("/admin/maintenance")
def AdminMaintenance():
	if not isAdmin(current_user):
		return abort(404)

	return render_template('admin_maintenance.html', maintenance=maintenance)

@login_required
@app.route("/admin/maintenance/<action>")
def AdminMaintenanceAction(action):
	if not isAdmin(current_user):
		return abort(404)

	global maintenance
	if action == "enable":
		maintenance = True
	elif action == "disable":
		maintenance = False
	Log("admin", current_user.username + " updated maintenance mode to " + ("on" if maintenance else "off"))
	return redirect("/admin/maintenance")

@app.route("/admin/logout")
def AdminLogout():
	logout_user()
	return redirect("/admin")

def Backup():
	#get date and time
	now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
	os.makedirs("backup/" + now, exist_ok=True)

	#copy database
	shutil.copy("instance/cardwarskingdom.db", f"backup/{now}/cardwarskingdom.db")

	#copy persist folder
	shutil.copytree("data/persist", f"backup/{now}/persist")

	#zip
	shutil.make_archive("backup/" + now, 'zip', "backup/" + now)

	#delete folder
	shutil.rmtree("backup/" + now)

	Log("admin", "Backed up")

	return True

@login_required
@app.route("/admin/logs", methods=['GET'])
def AdminLogs():
	if not isAdmin(current_user):
		return abort(404)    

	perpage = 20
	pagerequest = request.args.get('page', 1, type=int)
	query = request.args.get('query', '', type=str)
	logs = db.paginate(db.select(Logs).order_by(Logs.id.desc()), page=pagerequest, per_page=perpage)

	if query != '':
		logs = db.paginate(db.select(Logs).filter(Logs.player == query).order_by(Logs.id.desc()), page=pagerequest, per_page=perpage)

	return render_template('admin_logs.html', logs=logs, query=query)

@login_required
@app.route("/admin/upsight", methods=['GET'])
def AdminUpsight():
	if not isAdmin(current_user):
		return abort(404)

	perpage = 20
	pagerequest = request.args.get('page', 1, type=int)
	query = request.args.get('query', '', type=str)
	logs = db.paginate(db.select(UpsightLogs).order_by(UpsightLogs.id.desc()), page=pagerequest, per_page=perpage)

	if query != '':
		logs = db.paginate(db.select(UpsightLogs).filter(UpsightLogs.player_id == query).order_by(UpsightLogs.id.desc()), page=pagerequest, per_page=perpage)

	#convert time
	for log in logs.items:
		log.time = datetime.fromtimestamp(log.time)

	return render_template('admin_upsight.html', logs=logs, query=query)

class Bans(db.Model):
	username = db.Column(db.String(80), primary_key=True)
	bantype = db.Column(db.String(80), nullable=False)
	author = db.Column(db.String(80), nullable=True)
	time = db.Column(db.Integer, nullable=True, default=int(time.time()))

	def as_dict(self):
		return {c.name: getattr(self, c.name) for c in self.__table__.columns}

class Logs(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	date = db.Column(db.String(80), nullable=False)
	time = db.Column(db.String(80), nullable=False)
	player = db.Column(db.String(80), nullable=False)
	ip = db.Column(db.String(80), nullable=True)
	message = db.Column(db.String(8192), nullable=False)

class UpsightLogs(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	player_id = db.Column(db.String(80), nullable=False)
	time = db.Column(db.Integer, nullable=False, default=int(time.time()))
	event = db.Column(db.String(80), nullable=False)
	action = db.Column(db.String(80), nullable=False)
	message = db.Column(db.String(1024), nullable=True)

def PlayerLog(ip:str,player:str, message:str):
	db_log = Logs(date=datetime.now().strftime("%Y-%m-%d"), time=datetime.now().strftime("%H:%M:%S"), player=player, ip=ip, message=message)
	db.session.add(db_log)
	db.session.commit()

def IPFromRequest(request:Request):
	ip = request.remote_addr
	if request.headers.getlist("X-Forwarded-For"):
		ip = request.headers.getlist("X-Forwarded-For")[0]
	return ip


class Player(db.Model):
	username = db.Column(db.String(80), primary_key=True, unique=True, nullable=False)
	game = db.Column(db.String(8192), nullable=True)
	multiplayer_name = db.Column(db.String(128), nullable=True)
	icon = db.Column(db.String(128), nullable=True)
	deck = db.Column(db.String(1024), nullable=True)
	deck_rank = db.Column(db.String(16), nullable=True)
	landscapes = db.Column(db.String(1024), nullable=True)
	helper_creature = db.Column(db.String(1024), nullable=True)
	leader = db.Column(db.String(128), nullable=True)
	leader_level = db.Column(db.Integer, nullable=True)
	allyboxspace = db.Column(db.Integer, nullable=True)
	level = db.Column(db.Integer, nullable=True)
	friends = db.Column(db.String(8192), nullable=True, default="[]")
	friend_requests = db.Column(db.String(8192), nullable=True, default="[]")
	last_online = db.Column(db.Integer, nullable=True, default=int(time.time()))
	helpcount = db.Column(db.Integer, nullable=True, default=0)
	anonymoushelpcount = db.Column(db.Integer, nullable=True, default=0)
	devicename = db.Column(db.String(128), nullable=True)
 
	def as_dict(self):
		return {c.name: getattr(self, c.name) for c in self.__table__.columns}

@app.route("/")
def Index():
	return "200 App server running"

@app.route("/persist/static/manifest.json")
def Manifest():
	with open("data/persist/manifest.json", "r") as f:
		return f.read()

#only works in v1.18.0
@app.route("/persist/static/blueprints", methods=['GET'])
def Blueprints():
	data = []
	for root, dirs, files in os.walk("data/persist/blueprints"):
		for file in files:
			data.append({
				"name": file.replace(".json", ""),
				"data": open(f"{root}/{file}", "r").read()
			})
	return jsonify(data)

#only works in v1.0.16 - v1.0.17
@app.route("/persist/static/Blueprints/<string:file>", methods=['GET'])
def BlueprintsLegacy(file:str):
	if not file.startswith("db_") and not file.endswith(".json"):
		return make_response("Blueprint not found!", 404)
	if not os.path.exists(f"data/persist/blueprints/{file}"):
		return make_response("Blueprint not found!", 404)
	return open(f"data/persist/blueprints/{file}", "r").read()

@app.route("/account/preAuth/")
def AccountPreAuth():
	data = {
		"data": {
			"nonce": os.urandom(32).hex()
		}
	}
	return jsonify(data)

@app.route("/account/gcAuth/", methods=['POST'])
def AccountGCAuth():
	clientData = parse_qs(request.get_data().decode('utf-8'))
	clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
 
	#Create user if it doesn't exist
	db_user = Player.query.filter_by(username=clientData["player_id"]).first()
 
	isplayernew = False
 
	if db_user is None:
		db_user = Player(username=clientData["player_id"])
		db.session.add(db_user)
		db.session.commit()
		isplayernew = True
		PlayerLog(ip=IPFromRequest(request), player=clientData["player_id"], message="Created new player")     

	data = {
		"data": {
			"user_id": clientData["player_id"],
			"is_new": isplayernew
		}
	}
	return data

@app.route("/persist/getcc/")
def GetCountryCode():
	data = {
		"ip": request.headers.get("X-Forwarded-For", request.remote_addr),
		"country_code": "US"
	}
	return jsonify(data)

@app.route("/multiplayer/new_player/", methods=['POST'])
def MultiplayerNewPlayer():
	clientData = parse_qs(request.get_data().decode('utf-8'))
	clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}

	db_user = Player.query.filter_by(username=clientData["player_id"]).first()
	if db_user is None:
		return make_response("No player found!", 404)

	db_user.multiplayer_name = clientData["name"]
	db_user.icon = clientData["icon"]
	db_user.deck_rank = clientData["deck_rank"]
	db_user.landscapes = clientData["landscapes"]
	db_user.helper_creature = clientData["helper_creature"]
	db_user.leader = clientData["leader"]
	db_user.leader_level = clientData["leader_level"]
	db_user.allyboxspace = clientData["allyboxspace"]
	db_user.level = clientData["level"]
	db.session.commit()


 
	return jsonify({
		"success": True,
		"data": {
			"name": clientData["name"],
			"icon": clientData["icon"],
			"leader": clientData["leader"],
			"level": str(clientData["leader_level"]),
			"trophies": "0" #TODO
		}
	})	

@app.route("/multiplayer/update_deck_name/", methods=['POST'])
def MultiplayerUpdateDeckName():
	clientData = parse_qs(request.get_data().decode('utf-8'))
	clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}

	db_user = Player.query.filter_by(username=clientData["player_id"]).first()
	if db_user is None:
		return make_response("No player found!", 404)

	db_user.deck_rank = clientData["deck_rank"]
	db_user.landscapes = clientData["landscapes"]
	db_user.helper_creature = clientData["helper_creature"]
	db_user.leader = clientData["leader"]
	db_user.leader_level = clientData["leader_level"]
	db_user.allyboxspace = clientData["allyboxspace"]
 
	db.session.commit()
  
	return jsonify({
		"success": True
	})

@app.route("/persist/user_action2/", methods=['POST'])
def UserAction2():
	clientData = parse_qs(request.get_data().decode('utf-8'))
	clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	
	UpdateLastOnline(clientData["player_id"])
 
	#Check if an event was sent
	if "evt" in clientData:
		db_user = Player.query.filter_by(username=clientData["player_id"]).first()
		if db_user is None:
			return make_response("No player found!", 404)
		
		FreeHardCurrency = int(clientData["fr"])
		df = int(clientData["df"])
  
		finalamount = FreeHardCurrency + df
  
		data = {
			"success": True,
			"data": "{\"fields\": {\"level2\": " + str(finalamount) + "}}"
		}
	else:
		data = {
			"success": True,
		}
	
	return jsonify(data) 

@app.route("/persist/<username>/game", methods=['GET', 'PUT'])
def PersistGame(username):
	UpdateLastOnline(username)
	
	if request.method == 'GET':
		db_user = Player.query.filter_by(username=username).first()
		if db_user is None:
			return make_response("No game found!", 404)
		if db_user.game is None:
			return make_response("No game found!", 404)
		if db_user.game == b"" or db_user.game == b" ":
			return make_response("No game found!", 404)
		return db_user.game

	if request.method == 'PUT':
		data = request.data
		db_user = Player.query.filter_by(username=username).first()
		if db_user is None:
			return make_response("No game found!", 404)
		db_user.game = data
		db.session.commit()
		return make_response("OK", 200)


def UpdateLastOnline(player_id):
	user = Player.query.filter_by(username=player_id).first()
	if user is None:
		return None
	user.last_online = int(time.time())
	db.session.commit()

def AllyBoxSpaceNotExceeded(player_id):
	user = Player.query.filter_by(username=player_id).first()
	if user is None:
		return None

	#count number of friends
	friends = json.loads(user.friends)
 
	friends_count = 0
 
	for friend in friends:
		friend_user = Player.query.filter_by(username=friend).first()
		if friend_user is None:
			continue

		friends_count += 1
	
	return friends_count < user.allyboxspace

@app.route("/persist/friends/<string:player_id>")
def PersistFriends(player_id):
	UpdateLastOnline(player_id)
	db_user = Player.query.filter_by(username=player_id).first()
 
	if db_user is None:
		return make_response("No player found!", 404)

	data = []
 
	player_friends = json.loads(db_user.friends)
 
	for friend in player_friends:
		allyinfo = GetAllyInfo(friend, True)
		if allyinfo is not None:
			data.append(allyinfo)
 
	return jsonify(data)

@app.route("/persist/friends_find_candidatesDW/", methods=['POST'])
def PersistFriendsFindCandidates():
	clientData = parse_qs(request.get_data().decode('utf-8'))
	clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
 
	db_user = Player.query.filter_by(username=clientData["player_id"]).first()
 
	if db_user is None:
		return make_response("No player found!", 404)

	data = []
 
	player_friends = json.loads(db_user.friends)
 
	for friend in player_friends:
		allyinfo = GetAllyInfo(friend, True)
		if allyinfo is not None:
			data.append(allyinfo)
  
	data2 = {
		"success": True,
		"data": json.dumps(data)
	}
	return jsonify(data2)

@app.route("/persist/friends_use_friendDW/", methods=['POST'])
def PersistFriendsUseFriend():
	clientData = parse_qs(request.get_data().decode('utf-8'))
	clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
 
 
	db_ally = Player.query.filter_by(username=clientData["friendid"]).first()

	if db_ally is None:
		return make_response("No player found!", 500)

	db_ally.helpcount = int(db_ally.helpcount) + 1
	db.session.commit()
 
	data = {
		"success": True,
	}
	return jsonify(data)

@app.route("/persist/friends_request_withmyinfoDW/", methods=['POST'])
def PersistFriendsRequestWithMyInfo():
	clientData = parse_qs(request.get_data().decode('utf-8'))
	clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
 
	UpdateLastOnline(clientData["player_id"])
	
	try:
		invite_user = Player.query.filter_by(username=clientData["invite_id"].replace("_", "-")).first()
	except:
		return make_response("No player found!", 400)
	if invite_user is None:
		return make_response("No player found!", 400)

	db_user = Player.query.filter_by(username=clientData["player_id"]).first()
	if db_user is None:
		return make_response("No player found!", 400)


	inviteuserfr = json.loads(invite_user.friend_requests)

	#Player ally check
	allycheck = AllyBoxSpaceNotExceeded(clientData["player_id"])
	if allycheck == False:
		return jsonify({
			"success": True,
			"info": "exceed me"
		})
  
	#friend ally check
	friendallycheck = AllyBoxSpaceNotExceeded(clientData["invite_id"].replace("_", "-"))
	if friendallycheck == False:
		return jsonify({
			"success": True,
			"info": "exceed"
		})
 
	#check if player already sent a request
	if clientData["player_id"] not in invite_user.friend_requests:
		inviteuserfr.append(clientData["player_id"])
		invite_user.friend_requests = json.dumps(inviteuserfr)
		db.session.commit()
		return jsonify({
			"success": True
		})
	else:
		return jsonify({
			"success": True,
			"info": "duplicate"
		})
  
def GetAllyInfo(player_id: str, isally: bool):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return None
	if db_user.multiplayer_name is None:
		return None
	data = {
		"fields": {
			"user_id": db_user.username,
			"name": db_user.multiplayer_name,
			"icon": db_user.icon,
			"rankxp": db_user.leader_level,
			"helpcount": db_user.helpcount if db_user.helpcount is not None else "0",
			"anonymoushelpcount": db_user.anonymoushelpcount if db_user.anonymoushelpcount is not None else "0",
			"helpercreatureid": db_user.leader,
			"helpercreature": db_user.helper_creature,
			"landscapes": db_user.landscapes,
			"ally": "1" if isally else "0",
			"sincelastactivedate": str(int(time.time()) - db_user.last_online)	
		}
	}
	return data

@app.route("/persist/friends_all_requests_received/<string:player_id>", methods=['GET'])
def PersistFriendsAllRequestsReceived(player_id):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 400)

	data = []
 
	playerfriendrequests = json.loads(db_user.friend_requests)
 
	for friendrequest in playerfriendrequests:
		allyinfo = GetAllyInfo(friendrequest, False)
		if allyinfo is not None:
			data.append(allyinfo)
 
	return jsonify(data)

@app.route("/persist/friends_deny_request/<string:player_id>/<string:invite_id>", methods=['GET'])
def PersistFriendsDenyRequest(player_id, invite_id):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 400)

	UpdateLastOnline(player_id)	
 
	#remove friend request
	player_requests = json.loads(db_user.friend_requests)
	player_requests.remove(invite_id)
	db_user.friend_requests = json.dumps(player_requests)
 
	db.session.commit()
	return jsonify({
		"success": True
	})
 
@app.route("/persist/friends_confirm_request_withmyinfoDW/", methods=['POST'])
def PersistFriendsConfirmRequestWithMyInfo():
	clientData = parse_qs(request.get_data().decode('utf-8'))
	clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
 
	UpdateLastOnline(clientData["player_id"])
 
	db_user = Player.query.filter_by(username=clientData["player_id"]).first()
	if db_user is None:
		return make_response("No player found!", 400)

	#Player ally check
	allycheck = AllyBoxSpaceNotExceeded(clientData["player_id"])
	if allycheck == False:
		return jsonify({
			"success": True,
			"info": "exceed me"
		})
  
	#friend ally check
	friendallycheck = AllyBoxSpaceNotExceeded(clientData["invite_id"])
	if friendallycheck == False:
		return jsonify({
			"success": True,
			"info": "exceed"
		})

	#remove friend request
	player_requests = json.loads(db_user.friend_requests)
	player_requests.remove(clientData["invite_id"])
	db_user.friend_requests = json.dumps(player_requests)
 
	#add friend
	player_friends = json.loads(db_user.friends)
	player_friends.append(clientData["invite_id"])
	db_user.friends = json.dumps(player_friends)
 
	#add self to friend's friend list
	friend_user = Player.query.filter_by(username=clientData["invite_id"]).first()
 
	if friend_user is None:
		return make_response("No player found!", 400)

	friend_friends = json.loads(friend_user.friends)
	friend_friends.append(clientData["player_id"])
	friend_user.friends = json.dumps(friend_friends)
 
	db.session.commit()
 
	return jsonify({
		"success": True
	})
 
@app.route("/persist/friends_remove/<string:player_id>/<string:invite_id>", methods=['GET'])
def PersistFriendsRemove(player_id, invite_id):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 400)

	#remove friend
	player_friends = json.loads(db_user.friends)
	player_friends.remove(invite_id)
	db_user.friends = json.dumps(player_friends)
 
	#remove self from friend
	friend_user = Player.query.filter_by(username=invite_id).first()
 
	if friend_user is None:
		return make_response("No player found!", 400)

	friend_friends = json.loads(friend_user.friends)
	friend_friends.remove(player_id)
	friend_user.friends = json.dumps(friend_friends)
	
	db.session.commit()
 
	return jsonify({
		"success": True
	})


if __name__ == '__main__':
	app.run(debug=args.debug, port=args.port)

with app.app_context():
	db.create_all()

scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.start()