from google.appengine.runtime import DeadlineExceededError
from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util
from google.appengine.api import urlfetch
from google.appengine.api import taskqueue
from google.appengine.api import channel
from google.appengine.api import memcache
from time import time
import logging

import simplejson
import random, datetime

globHighId = 2**30

def memcache_accessor(key, value=None):
        if value != None:
                logging.info("setting %s to %s in memcache"%(key, value))
                memcache.set(key, value, 60*15) #15 minutes
        return memcache.get(key)
def histories(name, value=None):
        return memcache_accessor(name+"History", value)
def tickers(name, value=None):
        return memcache_accessor(name+"Tickers", value)
def banned(address, value=None):
        return memcache_accessor(address+"Banned", value)
def until(address, value=None):
        return memcache_accessor(address+"Until",  value)

def exchanges():
        return [('https://mtgox.com/code/data/ticker.php', 'MtGox'),
                ('https://api.tradehill.com/APIv1/USD/Ticker', 'TradeHill')]



class RetrieveHandler(webapp.RequestHandler):
        def get(self):
                for url, name in exchanges():
                        history = histories(name)
                        ticker = None
                        try:
                                logging.info("I am fetching the ticker data for %s"%name)
                                result = urlfetch.fetch(url)
                                ticker = simplejson.loads(result.content)['ticker']
                                logging.info("retrieved from %s the ticker data %s"%(name, ticker))
                                ticker['exchange'] = name
                                tickers(name, ticker)
                                history.append(ticker['last'])
                        except (TypeError, DeadlineExceededError):
                                try:
                                        history.append(history[-1])
                                        logging.warning("%s failed, cloned last value (%s)"%(name, result.status_code))
                                except:
                                        history = []
                                        logging.warning("%s had no history, set to []"%name)
                        finally:
                                histories(name, history[::-1][0:1440][::-1])
                                taskqueue.add(url="/tasks/notify", params={'min': 0,
                                                                           'max':globHighId, 
                                                                           'ticker':simplejson.dumps(ticker)})


class NotifyWorker(webapp.RequestHandler):
        def post(self):
                minimum = int(self.request.get('min'))
                maximum = int(self.request.get('max'))
                ticker  = self.request.get('ticker')
                logging.info("min:%s max:%s ticker:%s"%(minimum, maximum, ticker))
                query = Channel.all().filter('channel_id >=', minimum).filter('channel_id <', maximum).fetch(2)
                logging.info("a total of "+str(len(query))+" channel objects were detected (max 2)")
                if len(query) == 2:
                        midpoint = int(minimum + ((maximum - minimum) / 2.0))
                        if midpoint == minimum: midpoint = minimum + 1
                        logging.info("midpoint was "+str(midpoint))
                        taskqueue.add(url="/tasks/notify", params={'min': minimum, 'max':midpoint, 'ticker':ticker})
                        taskqueue.add(url="/tasks/notify", params={'min': midpoint, 'max':maximum, 'ticker':ticker})
                elif len(query) == 1:
                        logging.info(ticker)
                        channel.send_message(str(query[0].channel_id), ticker)

                
class GetIdHandler(webapp.RequestHandler):
        def output(self, json):
                str_json = simplejson.dumps(json)
                self.response.out.write(str_json)


        def get(self):
                address = self.request.remote_addr
                is_banned = banned(address)
                period = until(address)
                t = time()
                
                if not is_banned:
                        is_banned = banned(address, t)
                        period = until(address, 1)
                elif period < 1024: 
                        until(address, period * 2)

                if t + 2 < is_banned + period: #+1 to ignore the first ban
                        self.output({'time':t, "msg":"Over taxing resources", "penalty_box_until":is_banned+period})
                else:
                        while(True):
                                id = random.randint(0, globHighId - 1)
                                if not Channel.all().filter("id = ", id).get(): break #not safe
                        token = channel.create_channel(str(id))
                        self.output(
                                dict(
                                        [('id',id), ('token',token)] +
                                        [(name, histories(name)) for _, name in exchanges()]))
                        
class Channel(db.Model):
        channel_id = db.IntegerProperty(required=True)
        created = db.DateTimeProperty(auto_now_add=True)

class ConnectedHandler(webapp.RequestHandler):
        def post(self):
                client_id = int(self.request.get('from'))
                logging.info("the client id was %s" % client_id)
                key = Channel(channel_id=client_id).put()
                print db.get(key).key()
                for _, name in exchanges():
                        ticker = tickers(name)
                        logging.info("ticker was => %s"%ticker)
                        json = simplejson.dumps(ticker)
                        logging.info("json was => %s"%json)
                        taskqueue.add(url="/tasks/notify", params={'min':    client_id,
                                                                   'max':    client_id + 1, 
                                                                   'ticker': json})
                        
class DisconnectedHandler(webapp.RequestHandler):
        def post(self):
                client_id = int(self.request.get('from'))
                logging.info("the client id disconnecting was %s" % client_id)
                Channel.all().filter("id = ", client_id).get().delete()
                
class DisconnectedWorker(webapp.RequestHandler):
        def get(self):
                time = datetime.datetime.now() - datetime.timedelta(hours=2, minutes=5)
                [x.delete() for x in Channel.all().filter("created <  ", time).fetch(1000)]
                        
def main():
        application = webapp.WSGIApplication(
                [       
                        ('/getId', GetIdHandler),
                        ('/tasks/disconnect', DisconnectedWorker),
                        ('/tasks/retrieve', RetrieveHandler),
                        ('/tasks/notify', NotifyWorker),
                        ('/_ah/channel/connected/', ConnectedHandler),
                        ('/_ah/channel/disconnected/', DisconnectedHandler)
                        ], 
                        debug=True)
        util.run_wsgi_app(application)

if __name__ == '__main__':
    main()
 
