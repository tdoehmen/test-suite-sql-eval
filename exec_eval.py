import os
import re
import duckdb
import asyncio
import threading
from typing import Tuple, Any, List, Set
from itertools import product
from collections import defaultdict
import tqdm
import random
import time
import pickle as pkl
import subprocess
from itertools import chain
import shutil
from pathlib import Path
from .parse import get_all_preds_for_execution, remove_distinct


threadLock = threading.Lock()
TIMEOUT = 60
TMP_DIR = "_tmp"
EXEC_TMP_DIR = os.path.join(os.path.dirname(__file__), "tmp")


def permute_tuple(element: Tuple, perm: Tuple) -> Tuple:
    assert len(element) == len(perm)
    return tuple([element[i] for i in perm])


def unorder_row(row: Tuple) -> Tuple:
    return tuple(sorted(row, key=lambda x: str(x) + str(type(x))))


def tuple_sublists(row: Tuple) -> Tuple:
    new_row = []
    for item in row:
        if isinstance(item, list):
            new_row.append(tuple(item))
        else:
            new_row.append(item)
    new_row = tuple(new_row)
    return new_row


# unorder each row in the table
# [result_1 and result_2 has the same bag of unordered row]
# is a necessary condition of
# [result_1 and result_2 are equivalent in denotation]
def quick_rej(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    s1 = [unorder_row(row) for row in result1]
    s2 = [unorder_row(row) for row in result2]
    if order_matters:
        return s1 == s2
    else:
        return set(s1) == set(s2)


# return whether two bag of relations are equivalent
def multiset_eq(l1: List, l2: List) -> bool:
    if len(l1) != len(l2):
        return False
    d = defaultdict(int)
    for e in l1:
        d[e] = d[e] + 1
    for e in l2:
        d[e] = d[e] - 1
        if d[e] < 0:
            return False
    return True


def get_constraint_permutation(tab1_sets_by_columns: List[Set], result2: List[Tuple]):
    num_cols = len(result2[0])
    perm_constraints = [{i for i in range(num_cols)} for _ in range(num_cols)]
    if num_cols <= 3:
        return product(*perm_constraints)

    # we sample 20 rows and constrain the space of permutations
    for _ in range(20):
        random_tab2_row = random.choice(result2)

        for tab1_col in range(num_cols):
            for tab2_col in set(perm_constraints[tab1_col]):
                if random_tab2_row[tab2_col] not in tab1_sets_by_columns[tab1_col]:
                    perm_constraints[tab1_col].remove(tab2_col)
    return product(*perm_constraints)


# check whether two denotations are correct
def result_eq(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    if len(result1) == 0 and len(result2) == 0:
        return True

    # if length is not the same, then they are definitely different bag of rows
    if len(result1) != len(result2):
        return False

    num_cols = len(result1[0])

    # if the results do not have the same number of columns, they are different
    if len(result2[0]) != num_cols:
        return False

    result1 = [tuple_sublists(row) for row in result1]
    result2 = [tuple_sublists(row) for row in result2]

    # unorder each row and compare whether the denotation is the same
    # this can already find most pair of denotations that are different
    if not quick_rej(result1, result2, order_matters):
        return False

    # the rest of the problem is in fact more complicated than one might think
    # we want to find a permutation of column order and a permutation of row order,
    # s.t. result_1 is the same as result_2
    # we return true if we can find such column & row permutations
    # and false if we cannot
    tab1_sets_by_columns = [{row[i] for row in result1} for i in range(num_cols)]

    # on a high level, we enumerate all possible column permutations that might make result_1 == result_2
    # we decrease the size of the column permutation space by the function get_constraint_permutation
    # if one of the permutation make result_1, result_2 equivalent, then they are equivalent
    for perm in get_constraint_permutation(tab1_sets_by_columns, result2):
        if len(perm) != len(set(perm)):
            continue
        if num_cols == 1:
            result2_perm = result2
        else:
            result2_perm = [permute_tuple(element, perm) for element in result2]
        if order_matters:
            if result1 == result2_perm:
                return True
        else:
            # in fact the first condition must hold if the second condition holds
            # but the first is way more efficient implementation-wise
            # and we use it to quickly reject impossible candidates
            if set(result1) == set(result2_perm) and multiset_eq(result1, result2_perm):
                return True
    return False


def replace_cur_year(query: str) -> str:
    return re.sub(
        "YEAR\s*\(\s*CURDATE\s*\(\s*\)\s*\)\s*", "2020", query, flags=re.IGNORECASE
    )

class WithDuckDBConnectionInTmpDir(object):
    def __init__(self, databases_file, tmp_dir):
        if not os.path.exists(databases_file):
            raise Exception("Database note found: %s" % databases_file)
        os.makedirs(tmp_dir)
        shutil.copy(databases_file, tmp_dir)
        self.tmp_dbfile = Path(databases_file).name
        self.tmp_dir = tmp_dir
        self.original_wd = os.getcwd()

    def __enter__(self):
        os.chdir(self.tmp_dir)
        self.con = duckdb.connect(self.tmp_dbfile)
        return self.con

    def __exit__(self, *args):
        self.con.close()
        os.chdir(self.original_wd)
        shutil.rmtree(self.tmp_dir)

async def exec_on_db_(duckdb_path: str, query: str, setup_sql: str, validate_sql: str) -> Tuple[str, Any]:
    #query = replace_cur_year(query)
    try:
        with WithDuckDBConnectionInTmpDir(duckdb_path, TMP_DIR) as connection:
            if setup_sql is not None:
                print("Running Setup SQL:" + setup_sql)
                connection.execute(setup_sql)
            ddb_benchmark_result_rel = connection.sql(query)
            if ddb_benchmark_result_rel is not None:
                connection.execute("CREATE TABLE ddb_benchmark_result AS SELECT * FROM ddb_benchmark_result_rel")
            else:
                connection.execute("CREATE TABLE ddb_benchmark_result(empty TEXT)")
            print("Running Validation SQL:" +validate_sql)
            result = connection.execute(validate_sql).fetchall()
            return "result", result
    except Exception as e:
        return "exception", e


async def exec_on_db(
    duckdb_path: str, query: str, setup_sql: str, validate_sql: str, timeout: int = TIMEOUT
) -> Tuple[str, Any]:
    try:
        return await asyncio.wait_for(exec_on_db_(duckdb_path, query, setup_sql, validate_sql), timeout)
    except asyncio.TimeoutError:
        return ("exception", TimeoutError)
    except Exception as e:
        return ("exception", e)


# postprocess the model predictions to avoid execution errors
# e.g. removing spaces between ">" and "="
def postprocess(query: str) -> str:
    query = query.replace("> =", ">=").replace("< =", "<=").replace("! =", "!=")
    return query


# approximate whether p_str and g_str are semantically equivalent
# db is the database path
# we are going to evaluate whether they are equivalent in all the databases
# that are in the same directory as db
# 0 if denotationally equivalent
# 1 otherwise
# the meaning of each auxillary argument can be seen in the parser definition in evaluation.py
def eval_exec_match(
    db: str,
    p_str: str,
    g_str: str,
    setup_sql: str,
    validate_sql: str,
    plug_value: bool,
    keep_distinct: bool,
    progress_bar_for_each_datapoint: bool,
) -> int:
    # post-process the prediction.
    # e.g. removing spaces between ">" and "="
    p_str, g_str = postprocess(p_str), postprocess(g_str)
    if not keep_distinct:
        try:
            # if sqlparse can't parse p_str, we should not even try to execute it
            p_str = remove_distinct(p_str)
        except Exception as e:
            return 0
        g_str = remove_distinct(g_str)

    # we decide whether two denotations are equivalent based on "bag semantics"
    # https://courses.cs.washington.edu/courses/cse444/10sp/lectures/lecture16.pdf
    # if there is order by in query, then we assume order of the rows matter
    # order by might also be used to find the max/min instead of sorting,
    # but in that case the result mostly only contains one row and hence order_matters does not make a difference
    order_matters = "order by" in g_str.lower()

    # find all databases in the same directory
    db_dir = os.path.dirname(db)
    db_paths = [
        os.path.join(db_dir, basename)
        for basename in os.listdir(db_dir)
        if ".duckdb" in basename
    ]

    preds = [p_str]
    # if plug in value (i.e. we do not consider value prediction correctness)
    # enumerate all ways to plug in values in the gold query to the model predictions
    # otherwise, we only evaluate the predicted query with its own value prediction
    if plug_value:
        _, preds = get_all_preds_for_execution(g_str, p_str)
        # we did not add this line in our EMNLP work
        # this reduces "false negatives" when value is substituted
        preds = chain([p_str], preds)

    for pred in preds:
        pred_passes = 1
        # compare the gold and predicted denotations on each database in the directory
        # wrap with progress bar if required
        if progress_bar_for_each_datapoint:
            ranger = tqdm.tqdm(db_paths)
        else:
            ranger = db_paths

        for db_path in ranger:
            g_flag, g_denotation = asyncio.run(exec_on_db(db_path, g_str, setup_sql=setup_sql, validate_sql=validate_sql))
            p_flag, p_denotation = asyncio.run(exec_on_db(db_path, pred, setup_sql=setup_sql, validate_sql=validate_sql))

            # we should expect the gold to be succesfully executed on the database
            assert (
                g_flag != "exception"
            ), f"gold query {g_str} has error {g_denotation} on database file {db_path}"

            # wrong if execution fails
            if p_flag == "exception":
                pred_passes = 0

            # if denotations are not equivalent, the prediction must be wrong
            elif not result_eq(g_denotation, p_denotation, order_matters=order_matters):
                pred_passes = 0
            if pred_passes == 0:
                break

        # the model prediction has the same denotation as the gold for all databases
        if pred_passes == 1:
            return 1

    # none of the predictions passed
    return 0
