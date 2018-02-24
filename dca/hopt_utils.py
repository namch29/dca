import argparse
import pickle
import sys
from operator import itemgetter

from bson import SON
from hyperopt.mongoexp import MongoTrials
from matplotlib import pyplot as plt
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from strats.net_rl import QNetStrat


mongo_fail_msg = "Failed to connect to MongoDB. \
        Have you started mongod server in 'db' dir? \n \
        mongod --dbpath . --directoryperdb --journal --nohttpinterface --port 1234"


def hopt_bounds(stratclass, params):
    general = {
        'gamma': [0.65, 0.85],
        'alpha': [0.01, 0.04],
        'alpha_dec': [0.99999, 1.00],
        'epsilon': [0.3, 0.9],
        'epsilon_decay': [0.9999, 1.0],
        'beta': [7, 23],
        'n_step': [2, 40],
    }
    specific = {
        QNetStrat: {
            'net_lr': [1e-5, 9e-5],
            'net_lr_decay': [0.9, 0.96],
            'net_creep_tau': [0.001, 0.2]  # Assuming copy iter of 5
        }
    }
    for strat in specific:
        if isinstance(stratclass, strat):
            pass
    return None


def mongo_connect():
    try:
        client = MongoClient('localhost', 1234, document_class=SON, w=1, j=True)
        client.admin.command('ismaster')  # Check if connection is working
    except ServerSelectionTimeoutError:
        print(mongo_fail_msg)
        sys.exit(1)
    return client


class MongoConn(MongoTrials):
    """
    NOTE For some reason, 'db.col.someFunc' is not the same as
    'col = db['colname']; col.someFunc'. The latter seem to work, the former does not.
    """

    def __init__(self, fname, server='localhost', port=1234):
        """fname example: 'mongo:qlearnnet' or just 'qlearnnet'"""
        # e.g. 'mongo://localhost:1234/results-singhnet-net_lr-beta/jobs'
        self.fname = fname.replace('mongo:', '')
        url = f"mongo://{server}:{port}/{self.fname}/jobs"
        print(f"Attempting to connect to mongodb with url {url}")
        try:
            super().__init__(url)
            self.client = MongoClient(server, port, document_class=SON, w=1, j=True)
            self.client.admin.command('ismaster')  # Check if connection is working
            self.db = self.client[self.fname]
        except ServerSelectionTimeoutError:
            print("Failed to connect to MongoDB. \
            Have you started mongod server in 'db' dir? \n \
            mongod --dbpath . --directoryperdb --journal --nohttpinterface --port 1234")
            sys.exit(1)

    def get_pps(self):
        """Get problems params stored in mongo db, sorted by the time they were added"""
        # Does not use attachmens object
        col = self.db['pp']
        pps = []
        for ppson in col.find():
            pp = ppson.to_dict()
            pp['dt'] = pp['_id'].generation_time
            pps.append(pp)
        pps.sort(key=itemgetter('dt'))
        return pps

    def add_pp(self, pp):
        """Add problem params to mongo db"""
        if 'dt' in pp:
            del pp['dt']
        col = self.db['pp']
        col.insert_one(pp)

    def prune_suspended(self):
        """Remove jobs with status 'suspended'."""
        col = self.db['jobs']
        pre_count = col.count()
        col.delete_many({'results': {'status': 'suspended'}})
        count = col.count()
        print(f"Deleted {pre_count-count}, suspended jobs, current count {count}")


def mongo_decide_gpu_usage(mongo_uri, max_gpu_procs):
    """Decide whether or not to use GPU based on how many proceses currently use it"""
    # The DB should contain a collection 'gpu_procs' with one document,
    # {'gpu_procs': N}, where N is the current number of procs that utilize the GPU.
    client = mongo_connect()
    db = client[mongo_uri]
    col = db['gpu_procs']
    doc = col.find_one()
    if doc is None:
        col.insert_one({'gpu_procs': 0})
        doc = col.find_one()
    if doc['gpu_procs'] >= max_gpu_procs:
        print("MONGO decided not to use GPU")
        using_gpu = False
    else:
        print("MONGO increasing GPU proc count")
        col.find_one_and_update(doc, {'$inc': {'gpu_procs': 1}})
        using_gpu = True
    client.close()
    return using_gpu


def mongo_decrease_gpu_procs(mongo_uri):
    """Decrease GPU process count in Mongo DB. Fails if none are in use."""
    client = mongo_connect()
    db = client[mongo_uri]
    col = db['gpu_procs']
    doc = col.find_one()
    assert doc is not None
    assert doc['gpu_procs'] > 0
    print("MONGO decreaseing GPU proc count")
    col.find_one_and_update(doc, {'$inc': {'gpu_procs': -1}})
    client.close()


def mongo_reset_gpu_procs(mongo_uri=None):
    client = mongo_connect()
    if mongo_uri is None:
        dbnames = client.list_database_names()
    else:
        dbnames = [mongo_uri]
    for dbn in dbnames:
        try:
            db = client[dbn]
            col = db['gpu_procs']
            doc = col.find_one()
            if doc is not None:
                col.find_one_and_update(doc, {'$set': {'gpu_procs': 0}})
        except KeyError:
            pass
    client.close()


def mongo_list_dbs():
    """List all databases, their GPU process count and collections."""
    client = mongo_connect()
    for dbname in client.list_database_names():
        if dbname in ['admin', 'local']:
            continue
        db = client[dbname]
        gpup = db['gpu_procs'].find_one()
        if gpup is not None:
            gpup = gpup['gpu_procs']
        print(f"DB: {dbname}\n  GPU proc count: {gpup}")
        for colname in db.list_collection_names():
            col = db[colname]
            print(f"  Col: {colname}, count: {col.count()}")
    client.close()


def mongo_drop_empty():
    client = mongo_connect()
    for dbname in client.list_database_names():
        if dbname in ['admin', 'local']:
            continue
        if client[dbname]['jobs'].count() == 0:
            client.drop_database(dbname)
    client.close()


def hopt_results(trials):
    """Gather losses and corresponding params for valid results"""
    if type(trials) == MongoConn:
        attachments = trials.get_pps()
        valid = list(filter(lambda x: x['result']['status'] == 'ok', trials.trials))
        valid_results = []
        pkeys = valid[0]['misc']['vals'].keys()
        params = {key: [] for key in pkeys}
        for i, res in enumerate(valid):
            valid_results.append((res['result']['loss'], i))
            for pkey in pkeys:
                params[pkey].append(res['misc']['vals'][pkey][0])
    else:
        """Gather losses and corresponding params for valid results"""
        attachments = trials.attachments
        results = [(e['loss'], i, e['status']) for i, e in enumerate(trials.results)]
        valid_results = filter(lambda x: x[2] == 'ok', results)
        params = trials.vals
    return valid_results, params, attachments


def hopt_trials(fname):
    """Load trials from MongoDB or Pickle file, depending on 'hopt_fname'"""
    try:
        if fname.startswith("mongo"):
            return MongoConn(fname)
        else:
            fname = fname.replace('.pkl', '') + '.pkl'
            with open(fname, "rb") as f:
                return pickle.load(f)
    except FileNotFoundError:
        print(f"Could not find {fname}.")
        sys.exit(1)


def add_pp_pickle(trials, pp):
    """Add problem params to Trials object attachments, if it differs from
    the last one added"""
    att = trials.attachments
    if "pp0" not in att:
        att['pp0'] = pp
        return
    n = 0
    while f"pp{n}" in att:
        n += 1
    n -= 1
    if att[f'pp{n}'] != pp:
        att[f'pp{n+1}'] = pp


def hopt_best(trials, n=1, view_pp=True):
    if n == 1:
        b = trials.best_trial
        params = b['misc']['vals']
        fparams = ' '.join([f"--{key} {value[0]}" for key, value in params.items()])
        print(f"Loss: {b['result']['loss']}\n{fparams}")
        return
    try:
        valid_results, params, attachments = hopt_results(trials)
    except IndexError:
        print("Invalid MongoDB identifier")
        sys.exit(1)
    sorted_results = sorted(valid_results)
    print(f"Found {len(sorted_results)} valid trials")
    if view_pp and attachments:
        print(attachments)
    for lt in sorted_results[:n]:
        fparams = ' '.join([f"--{key} {value[lt[1]]}" for key, value in params.items()])
        print(f"Loss {lt[0]:.6f}: {fparams}")


def hopt_plot(trials):
    valid_results, params, attachments = hopt_results(trials)
    losses = [x[0] for x in valid_results]
    n_params = len(params.keys())
    for i, (param, values) in zip(range(n_params), params.items()):
        pl1 = plt.subplot(n_params, 1, i + 1)
        pl1.plot(values, losses, 'ro')
        plt.xlabel(param)
    plt.show()


def runner(inp=None):
    parser = argparse.ArgumentParser(
        description='DCA', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        'fname',
        type=str,
        nargs='?',
        help="File name or MongoDB data base name"
        "for hyperopt destination/source. Prepend 'mongo:' to MongoDB names",
        default=None)
    parser.add_argument(
        '--best',
        dest='hopt_best',
        metavar='N',
        nargs='?',
        type=int,
        help="Show N best params found and corresponding loss",
        default=0,
        const=1)
    parser.add_argument(
        '--plot',
        action='store_true',
        help="Plot each param against corresponding loss",
        default=False)
    parser.add_argument(
        '--list_dbs', action='store_true', help="(MongoDB) List databases", default=False)
    parser.add_argument(
        '--reset_gpu_procs',
        action='store_true',
        help="(MongoDB) Reset gpu proc count for all dbs",
        default=False)
    parser.add_argument(
        '--drop_empty_dbs',
        action='store_true',
        help="(MongoDB) Drop empty databases",
        default=False)
    parser.add_argument(
        '--prune_jobs',
        action='store_true',
        help="(MongoDB) Prune suspended jobs",
        default=False)
    parser.add_argument(
        '--clean',
        action='store_true',
        help="(MongoDB) Prune suspended jobs, drop empty dbs, reset gpu proc count",
        default=False)
    args = vars(parser.parse_args(inp))
    if args['list_dbs']:
        mongo_list_dbs()
        return
    elif args['drop_empty_dbs']:
        mongo_drop_empty()
        return
    elif args['reset_gpu_procs']:
        mongo_reset_gpu_procs(None)
        return
    fname = args['fname']
    trials = hopt_trials(fname)
    if args['hopt_best'] > 0:
        hopt_best(trials, args['hopt_best'], view_pp=True)
    elif args['plot']:
        hopt_plot(trials)
    elif args['prune_jobs']:
        trials.prune_suspended()
    elif args['clean']:
        mongo_drop_empty()
        trials.prune_suspended()
        mongo_reset_gpu_procs(None)

    if type(trials) == MongoConn:
        trials.client.close()


if __name__ == '__main__':
    runner()
