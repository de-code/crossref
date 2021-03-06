import logging
import time

import requests
import ratelimit
import tqdm


@ratelimit.rate_limited(15)
def api_query(component, timeout=60, **kwargs):
    """
    Query the Crossref API and return the requests.response. Pass URL
    parameters as keyword arguments, e.g. `rows=1000` for max throughput.
    See https://github.com/CrossRef/rest-api-doc/blob/master/rest_api.md.
    """
    url = f'https://api.crossref.org/{component}'
    response = requests.get(url, kwargs, timeout=timeout)
    return response


def query_all(
        component='works',
        batch_size=20,
        cursor=None,
        max_items=None,
        tqdm=tqdm.tqdm,
        ):
    """
    Return a generator of all Crossref items for the specified component.

    component : str
        Crossref query endpoint. See https://git.io/vyp7S
    batch_size : int
        items to return per API call.
    cursor : None or str
        Specify cursor for the works endpoint. Use '*' to start from scratch
        or specify an existing cursor returned from a previous query.
        To use an offset rather than a cursor for pagin, use None (default).
    max_items : int or None
        max items to yield. Disable with None.
    tqdm : tqdm.tqdm
        tqdm class for displaying the progress_bar. Pass tqdm.notebook for a
        Jupyter themed progress bar.
    """
    # Initialize position
    progress_bar = None
    incomplete = True
    successive_errors = 0
    rows = batch_size
    if not cursor:
        offset = 0

    while incomplete:

        # Perform the API call
        params = {'cursor': cursor} if cursor else {'offset': offset}
        try:
            response = api_query(component, rows=rows, **params)
        except Exception as e:
            successive_errors += 1
            msg = (f'Successive error {successive_errors}. '
                   f'Error from the following parameters:'
                   f'\n{params}\nWith error:\n{e}')
            logging.warning(msg)
            time.sleep(2 ** successive_errors)
            rows = int(0.75 * rows)
            continue

        # HTTP Request failed
        if response.status_code != 200:
            successive_errors += 1
            msg = (f'Successive error {successive_errors}. '
                   f'Status code {response.status_code} from {response.url}:'
                   f'\n{response.text}')
            logging.warning(msg)
            # Exponential backoff upon errors
            time.sleep(2 ** successive_errors)
            rows = int(0.75 * rows)
            continue

        # If successful, rollback the state of emergency
        successive_errors = 0
        rows = batch_size

        # Extract JSON payload
        result = response.json()

        # JSON payload is not okay
        if result.get('status') != 'ok':
            msg = f'{response.url} returned:\n{result}'
            logging.warning(msg)
            continue

        # Initialize progress_bar
        if progress_bar is None:
            total = result['message']['total-results']
            if max_items is not None:
                total = min(total, max_items)
            progress_bar = tqdm(desc=component, total=total)

        # Yield items
        remaining = total - progress_bar.n
        items = result['message']['items'][:remaining]
        yield from items

        # Update position
        if cursor:
            cursor = result['message']['next-cursor']
            logging.info(f'{progress_bar.n:,} queries complete. '
                         f'Cursor updated to {cursor}')
        else:
            try:
                # Fail if offset bug occurs https://git.io/vyjkL
                start_index = result['message']['query']['start-index']
                assert offset == start_index
            except KeyError:
                pass
            offset += len(items)
            logging.info(f'Offset updated to {offset}')
        progress_bar.update(len(items))
        incomplete = bool(items)
