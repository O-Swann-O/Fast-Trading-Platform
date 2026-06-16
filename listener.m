clear; clc;
fprintf('Starting listener...\n');

DATA_PORT       = 5000;
SIGNAL_PORT     = 5001;
PYTHON_HOST     = "127.0.0.1";
SAMPLE_RATE_SEC = 0.100;
STALE_LIMIT_SEC = 5.000;

CONIDS = uint32([12087792, 12087797, 15016059, 12087820, 14433401, 15016062, 39453441]);
N = numel(CONIDS);

uIn  = udpport("datagram", "LocalPort", DATA_PORT);
uOut = udpport("datagram");

idxMap = containers.Map('KeyType', 'uint32', 'ValueType', 'double');
for i = 1:N
    idxMap(CONIDS(i)) = i;
end
latestMid  = nan(1, N, 'single');
lastUpdate = -inf(1, N);

t0 = tic;
nextSample = toc(t0) + SAMPLE_RATE_SEC;

try
    while true
        while uIn.NumDatagramsAvailable > 0
            pkt = read(uIn, 1, "datagram");
            raw = pkt.Data;
            if numel(raw) == 12
                conId = typecast(uint8(raw(1:4)), 'uint32');
                price = typecast(uint8(raw(5:8)), 'single');
                if isKey(idxMap, conId)
                    i = idxMap(conId);
                    latestMid(i)  = price;
                    lastUpdate(i) = toc(t0);
                end
            end
        end

        now_s = toc(t0);
        if now_s >= nextSample
            nextSample = nextSample + SAMPLE_RATE_SEC;

            stale = (now_s - lastUpdate) > STALE_LIMIT_SEC;
            prices = latestMid;
            prices(stale) = single(NaN);

            [targets, confs] = calculateSignal(CONIDS, prices);

            emit_ts = uint32(posixtime(datetime('now', 'TimeZone', 'UTC')));
            for i = 1:N
                if stale(i)
                    continue;
                end
                outBuffer = [ ...
                    typecast(CONIDS(i),         'uint8'), ...
                    typecast(int32(targets(i)), 'uint8'), ...
                    typecast(single(confs(i)),  'uint8'), ...
                    typecast(emit_ts,           'uint8') ];
                write(uOut, outBuffer, "uint8", PYTHON_HOST, SIGNAL_PORT);
            end
        end

        pause(0.001);
    end
catch err
    fprintf('Listener died: %s\n', err.message);
    clear uIn uOut;
end