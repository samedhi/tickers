from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util
from google.appengine.api import urlfetch
from google.appengine.api import taskqueue
from google.appengine.api import channel
import logging

import simplejson
import random, datetime

globHighId = 2**30

#This does not sync across multiple servers.
globLast = {'TradeHill' : 0, 'MtGox': 0}

def lastTick():
	return '{"trade":'+str(globLast['TradeHill'])+', "mtgox":'+str(globLast['MtGox'])+'}'

class Channel(db.Model):
	id = db.IntegerProperty(required=True)
	created = db.DateTimeProperty(auto_now_add=True)

class RetrieveHandler(webapp.RequestHandler):
		def get(self):
			global globHighId
			history = get_history()
			result_gox = urlfetch.fetch('https://mtgox.com/code/data/ticker.php')
			if result_gox.status_code == 200:
				json = simplejson.loads(result_gox.content)
				last = float(json['ticker']['last'])
				globLast['MtGox'] = last
				logging.info("from Mt Gox, received:"+str(last))
				history.mtGox = history.mtGox[:1439] + [last] 
			else:
				logging.info("status code for Mt Gox was %s"%result_gox.status_code)
				
			result_trd = urlfetch.fetch('https://api.tradehill.com/APIv1/USD/Ticker')
			if result_trd.status_code == 200:
				json = simplejson.loads(result_trd.content)
				last = float(json['ticker']['last'])
				globLast['TradeHill'] = last
				logging.info("from TradeHill, received:"+str(last))
				history.tradeHill = history.tradeHill[:1439] + [last]
			else:
				logging.info("status code TradeHill was %s"%result_trd.status_code)
			
			history.put()
			taskqueue.add(url="/tasks/notify", params={'min': 0, 'max':globHighId, 'last': lastTick()})
			
class NotifyWorker(webapp.RequestHandler):
		def post(self):
			min = int(float(self.request.get('min')))
			max = int(float(self.request.get('max')))
			last = self.request.get('last')
			logging.info("min:"+str(min)+" max:"+str(max)+" last:"+str(last))
			query = Channel.all().filter("id <=", max).filter("id >=", min).fetch(2)
			logging.info("a total of "+str(len(query))+" channel objects were detected (max 2)")
			if len(query) == 2:
				mid = int(min + ((max - min) / 2.0))
				logging.info("midpoint was "+str(mid))
				taskqueue.add(url="/tasks/notify", params={'min': min, 'max':mid, 'last':last})
				taskqueue.add(url="/tasks/notify", params={'min': mid, 'max':max, 'last':last})
			elif len(query) == 1:
				channel.send_message(str(query[0].id), last);

class History(db.Model):
	mtGox = db.ListProperty(float, required=True)
	tradeHill = db.ListProperty(float, required=True)
				
def get_history():
	key = db.Key.from_path("History", "HistoryKey")
	history = db.get(key)
	if history == None:
		history = History(key=key, time=[], value=[])
		history.put()
	return history
		
class GetIdHandler(webapp.RequestHandler):
    def get(self):
			while(True):
				id = random.randint(0, globHighId - 1)
				#not thread safe, but good enough
				if not Channel.all().filter("id = ", id).get():
					break
			token = channel.create_channel(str(id))
			self.response.out.write('{ "id" : %s , "token" : "%s", "mtgoxHist" : %s , "tradeHist" : %s}' % 
				(id, token, get_history().mtGox, get_history().tradeHill))

class ConnectedHandler(webapp.RequestHandler):
	def post(self):
		global globLast
		client_id = int(self.request.get('from'))
		Channel(id=client_id).put()
		taskqueue.add(url="/tasks/notify", params={'min': client_id, 
																							 'max': client_id + 1, 
																							 'last': lastTick()})
			
class DisconnectedHandler(webapp.RequestHandler):
	def post(self):
		client_id = int(self.request.get('from'))
		Channel.all().filter("id = ", client_id).get().delete()
		
class DisconnectedWorker(webapp.RequestHandler):
	def get(self):
		time = datetime.datetime.now() - datetime.timedelta(hours=3)
		[x.delete() for x in Channel.all().filter("created <  ", time).fetch(1000)]
			
def main():
	application = webapp.WSGIApplication(
		[	
		  ('/getId', GetIdHandler),
			('/tasks/disconnect', DisconnectedWorker),
			('/tasks/retrieve', RetrieveHandler),
			('/tasks/notify', NotifyWorker),
		  ('/_ah/channel/connected/', ConnectedHandler),
			('/_ah/channel/disconnected/', DisconnectedHandler)], debug=True)
	util.run_wsgi_app(application)

if __name__ == '__main__':
    main()