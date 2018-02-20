import argparse
import pickle
import sys
from operator import itemgetter

from bson import SON
from hyperopt.mongoexp import MongoTrials
from matplotlib import pyplot as plt
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

port = 1234
"""
NOTE For some reason, 'db.col.someFunc' is not the same as
'col = db['colname']; col.someFunc'. The latter seem to work, the former does not.
"""

mongo_fail_msg = """
Failed to connect to MongoDB. Have you started mongod server in 'db' dir? \n
mongod --dbpath . --directoryperdb --journal --nohttpinterface --port 1234 """


def mongo_connect(server='localhost', port=1234):
    try:
        client = MongoClient(server, port, document_class=SON, w=1, j=True)
        client.admin.command('ismaster')  # Check if connection is working
    except ServerSelectionTimeoutError:
        print(mongo_fail_msg)
        sys.exit(1)
    return client


def hopt_results(fname, trials):
    """Gather losses and corresponding params for valid results"""
    if type(trials) is MongoTrials:
        uri = fname.replace('mongo:', '')
        attachments = get_pps_mongo(uri)
        valid = list(filter(lambda x: x['result']['status'] == 'ok', trials.trials))
        valid_results = []
        pkeys = valid[0]['misc']['vals'].keys()
        params = {key: [] for key in pkeys}
        for i, res in enumerate(valid):
            valid_results.append((res['result']['loss'], i))
            for pkey in pkeys:
                params[pkey].append(res['misc']['vals'][pkey][0])
    else:
        attachments = trials.attachments
        results = [(e['loss'], i, e['status']) for i, e in enumerate(trials.results)]
        valid_results = filter(lambda x: x[2] == 'ok', results)
        params = trials.vals
    return valid_results, params, attachments


def hopt_trials(fname):
    """Load trials from MongoDB or Pickle file, depending on 'hopt_fname'"""
    try:
        if fname.startswith("mongo"):
            # e.g. 'mongo://localhost:1234/results-singhnet-net_lr-beta/jobs'
            fname = f"mongo://localhost:1234/{fname.replace('mongo:', '')}/jobs"
            print(f"Attempting to connect to mongodb with url {fname}")
            return MongoTrials(fname)
        else:
            fname = fname.replace('.pkl', '') + '.pkl'
            with open(fname, "rb") as f:
                return pickle.load(f)
    except FileNotFoundError:
        print(f"Could not find {fname}.")
        sys.exit(1)
    except ServerSelectionTimeoutError:
        print(mongo_fail_msg)
        sys.exit(1)


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


def add_pp_mongo(mongo_uri, pp):
    """Add problem params to mongo db"""
    if 'dt' in pp:
        del pp['dt']
    client = mongo_connect()
    db = client[mongo_uri]
    col = db['pp']
    # See if given pp already exists in db, and if not, add it
    col.insert_one(pp)
    client.close()


def get_pps_mongo(mongo_uri):
    """Get problems params stored in mongo db, sorted by the time they were added"""
    # Does not use attachmens object, will store different loc
    client = mongo_connect()
    db = client[mongo_uri]
    col = db['pp']
    pps = []
    for ppson in col.find():
        pp = ppson.to_dict()
        pp['dt'] = pp['_id'].generation_time
        pps.append(pp)
    pps.sort(key=itemgetter('dt'))
    client.close()
    return pps


def mongo_prune_suspended(mongo_uri):
    """Remove jobs with status 'suspended'."""
    client = mongo_connect()
    db = client[mongo_uri]
    col = db['jobs']
    pre_count = col.count()
    col.delete_many({'results': {'status': 'suspended'}})
    count = col.count()
    print(f"Deleted {pre_count-count}, suspended jobs, current count {count}")
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
        print(f"DB: {dbname}, GPU proc count: {gpup}")
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


def hopt_best(fname, trials=None, n=1, view_pp=True):
    if trials is None:
        try:
            trials = hopt_trials(fname)
        except (FileNotFoundError, ValueError):
            sys.exit(1)
    # Something below here might throw AttributeError when mongodb exists but is empty
    if n == 1:
        b = trials.best_trial
        params = b['misc']['vals']
        fparams = ' '.join([f"--{key} {value[0]}" for key, value in params.items()])
        print(f"Loss: {b['result']['loss']}\n{fparams}")
        return
    try:
        valid_results, params, attachments = hopt_results(fname, trials)
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


def hopt_plot(fname):
    trials = hopt_trials(fname)
    valid_results, params, attachments = hopt_results(fname, trials)
    losses = [x[0] for x in valid_results]
    n_params = len(params.keys())
    for i, (param, values) in zip(range(n_params), params.items()):
        pl1 = plt.subplot(n_params, 1, i + 1)
        pl1.plot(values, losses, 'ro')
        plt.xlabel(param)
    plt.show()


def runner():
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
        '--list_dbs',
        action='store_true',
        help="(MongoDB) List MongoDB databases",
        default=False)
    parser.add_argument(
        '--drop_empty_dbs',
        action='store_true',
        help="(MongoDB) List MongoDB databases",
        default=False)
    parser.add_argument(
        '--prune_jobs',
        action='store_true',
        help="(MongoDB) Prune suspended jobs",
        default=False)
    args = vars(parser.parse_args())
    fname = args['fname']
    if args['hopt_best'] > 0:
        hopt_best(fname, None, args['hopt_best'], view_pp=True)
    elif args['plot']:
        hopt_plot(fname)
    elif args['list_dbs']:
        mongo_list_dbs(fname)
    elif args['drop_empty_dbs']:
        mongo_drop_empty()
    elif args['prune_jobs']:
        mongo_prune_suspended(fname)


if __name__ == '__main__':
    runner()