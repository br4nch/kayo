## Installation

# Database

```
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo apt install redis-server
sudo systemctl status postgresql

psql -U postgres
CREATE USER your_username WITH PASSWORD 'your_password';
ALTER USER your_username CREATEDB;
\q
```

```
python3.10 -m venv .venv
source .venv/bin/activate
python3.10 -m pip install -r requirements.txt

pm2 start main.py --name KAYO --interpreter python3.10
```

# Puppeteer

```
apt --fix-broken install -y
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb; sudo dpkg -i google-chrome-stable_current_amd64.deb

python3.10 -m pip install pyppeteer
```
