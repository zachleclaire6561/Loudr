import os
import re
import time
import requests
import datetime
import logging
import http.client
import urllib
import configparser
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
import warnings
import pytz
import transceiverProperties
import discord.ext
from discord.ext import tasks, commands
import asyncio
import tracemalloc
warnings.filterwarnings("ignore", message="The localize method is no longer necessary")
tracemalloc.start()

# Configurations, now replaced as a conf file!
config = configparser.ConfigParser()
config.read('config.ini')
maxPingTimeDiff = int(config.get("configurations", "maxPingTimeDiff"))
logFile = config.get("configurations", "logFile")
transceiverBandList = [list(map(int, array.split(','))) for array in config.get("configurations", "transceiverBandList").split(';')]

#Strings for logging
logMessageSent = config.get("logs", "logMessageSent")
logStart = config.get("logs", "logStart")
logSystemDown = config.get("logs", "logSystemDown")
logRadioClubPushedOutage = config.get("logs", "logRadioClubPushedOutage")
logSystemOnline =  config.get("logs", "logSystemOnline")
logReconnect = config.get("logs", "logReconnect")
logLastTransmission = config.get("logs", "logLastTransmission")
logCreateTransceivers = config.get("logs", "logCreateTransceivers")

#Paths of strings for push notifications
outageNotifPath = config.get("notifs", "outageNotifPath")
reconnectNotifPath = config.get("notifs", "reconnectNotifPath")

#secret stuff hidden in ~/.bashrc
pushoverToken = os.environ["pushoverApiKey"]
pushoverUser = os.environ["pushoverUser"]
discordToken = os.getenv('loudrBotKey')

#import messages to users as strings
with open(outageNotifPath, "r") as f:
	outageNotif = f.read()
with open(reconnectNotifPath, "r") as f:
	reconnectNotif = f.read()

#set up internal logger
logging.basicConfig(
level=logging.INFO,  # Log messages with level INFO or higher
format='%(asctime)s - %(levelname)s - %(message)s',  # Format of log messages
handlers=[logging.FileHandler(logFile)]  # Log messages to a file called 'log.log'
)
logging.getLogger('apscheduler').setLevel(logging.CRITICAL)

#set up a list of Transceivers using the band list
transceiverList = []
for transceiver in transceiverBandList:
	transceiverList.append(transceiverProperties.WsprTransceiver(transceiver))

#log all reated transceivers
logging.critical(logCreateTransceivers.format(str(transceiverBandList)))

#create scheduler
scheduler = BlockingScheduler()

#log a start with internal logger
logging.critical(logStart)

#discord bot setup stuff
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
discordChannelNum = 1100287191323783268

@client.event
async def on_ready():
	logging.critical(f'Discord bot logged in as {client.user}')
	#send message confirming it is alive:
	await sendMessageToRadio("Don't be naughty! Loudr is now watching 👀")
	await dbCheck()
	now = datetime.now()
	seconds_until_next_minute = 60 - now.second
	await asyncio.sleep(seconds_until_next_minute)
	dbCheck.start()

#uses pushover to send a message to radio club, will be replaced with discord
async def sendMessageToRadio(message):
	try:
		#send to pushover
		conn = http.client.HTTPSConnection("api.pushover.net:443")
		conn.request("POST", "/1/messages.json",
			urllib.parse.urlencode({
				"token": pushoverToken,
				"user": pushoverUser,
				"message": message,
			}), { "Content-type": "application/x-www-form-urlencoded" })
		conn.getresponse()
		#send to bot
		channel = client.get_channel(discordChannelNum)
		await channel.send(message)
		#log
		logging.critical(logMessageSent.format(message))
	except Exception as e:
		logging.error("Exception occurred in sendMessageToRadio:", exc_info=True)

#convers time in seconds to hours and mins
async def secondsToTimestamp(seconds):
	hours = seconds // 3600
	minutes = (seconds // 60) % 60
	return hours, minutes

#take in a bool for if everyone was pinged over the past 6 mins!!!
@tasks.loop(seconds=60.0)
async def dbCheck():
	try:
		#create items to return and push to user/logs
		updatedTransceiverList = []
		messageToPush = ""
		messageToLog = ""
		currentTime = int(time.time())
		global transceiverList
		for transceiver in transceiverList:
			#find hours and minutes since last ping
			epochTimeLastPing, secondsSinceLastPing, lastPingScraped = transceiver.findLastPing()
			hoursSinceLastPing, minutesSinceLastPing = await secondsToTimestamp(secondsSinceLastPing)
			#check if there is an outage. Outage is defined as if the time since last ping is greater than the set threshold
			if maxPingTimeDiff <= secondsSinceLastPing:
				#log a outage, set time until outage reping
				messageToLog += logSystemDown.format(str(transceiver.getBands())) + "\n"
				#notify radio club if radio club was not notified yet
				if not transceiver.getNotificationStatus():
					messageToLog += logRadioClubPushedOutage.format(str(transceiver.getBands())) + "\n"
					#append string to push
					messageToPush += outageNotif.format(str(transceiver.getBands()), hoursSinceLastPing, minutesSinceLastPing) + "\n"
					transceiver.changeNotificationStatus()
			#in the event there is not an outage, log an notify radio club if radio club throught there was an outage
			else:
				#log everything is working if it does work, set time until reping
				secondsUntilNextCheck = maxPingTimeDiff + epochTimeLastPing - currentTime + 1
				hoursUntilNextCheck, minutesUntilNextCheck = await secondsToTimestamp(secondsUntilNextCheck)
				messageToLog += logSystemOnline.format(str(transceiver.getBands())) +"\n"
				if transceiver.getNotificationStatus():
					transceiver.changeNotificationStatus()
					messageToLog += logReconnect.format(str(transceiver.getBands())) + "\n"
					#append message to push:
					messageToPush += reconnectNotif.format(str(transceiver.getBands())) + "\n"
			updatedTransceiverList.append(transceiver)
			messageToLog += logLastTransmission.format(str(transceiver.getBands()), lastPingScraped, hoursSinceLastPing, minutesSinceLastPing) + "\n"
		#log all updates
		logging.info(messageToLog)
		#push messages to user if string is not blank
		if messageToPush != "":
			await sendMessageToRadio(messageToPush)
		#update transceiver list for when function is run again in a min
		transceiverList = updatedTransceiverList

	except Exception as e:
		logging.error("Exception occurred in dbCheck:", exc_info=True)

#Easter egg i guess
@client.event
async def on_message(message):
        if message.author == client.user:
                return

        if 'iamloud' in message.content.lower().replace(" ", ""):
                await message.channel.send('I am Loudr')

client.run(discordToken)
