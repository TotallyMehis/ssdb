# SSDB - Source engine Server List Discord Bot
Creates a list of Source engine game servers and updates it regularly. Useful for small mod communities.

## Instructions
- Make sure you have Python 3 installed and in your PATH.
- Run `pip install -r requirements.txt`
- Configure `config/.ssdb_config.ini` (remove .template from name) with at least the bot token, channel id and the list method
- Run `run.bat` or `run.sh`

**Docker Instructions (Optional)**:
- Make sure you have Docker and `docker-compose` in your PATH
- Run (docker) `docker-compose up --build` or `docker-compose up -d`
