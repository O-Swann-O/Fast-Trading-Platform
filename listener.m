clear; clc;

fprintf('Starting listener...\n');

% Config
DATA_PORT       = 5000;              % Inbound UDP ticks
SIGNAL_PORT     = 5001;              % Outbound UDP signals
TARGET_ID       = uint32(12087792);  % Target asset
PYTHON_HOST     = "127.0.0.1";       % Loopback
SAMPLE_RATE_SEC = 0.100;             % 100ms time step (10 Hz)
STALE_LIMIT_SEC = 5.000;             % 5-second staleness threshold

% Initialize Sockets & State
uIn  = udpport("datagram", "LocalPort", DATA_PORT);
uOut = udpport("datagram");

% Shared state variables 
latestPrice     = single(NaN);
latestTimestamp = uint32(0);
lastLocalTick   = datetime('now');

% initialize clock
tic;
nextSampleTime = toc + SAMPLE_RATE_SEC;

try
    while true
        % 1. THE CATCHER PHASE 
        while uIn.NumDatagramsAvailable > 0
            packet = read(uIn, 1, "datagram");
            raw    = packet.Data;

            if length(raw) == 12
                conId     = typecast(raw(1:4), 'uint32');
                price     = typecast(raw(5:8), 'single');
                timestamp = typecast(raw(9:12), 'uint32');

                if conId == TARGET_ID
                    latestPrice     = price;      % Instantly overwrite reality
                    latestTimestamp = timestamp;  % Broker timestamp
                    lastLocalTick   = datetime('now'); % Local backup clock
                end
            end
        end

        % 2.SAMPLER PHASE 
        currentTime = toc;
        if currentTime >= nextSampleTime
            nextSampleTime = nextSampleTime + SAMPLE_RATE_SEC; 
            secondsSinceLastTick = seconds(datetime('now') - lastLocalTick);
            
            if secondsSinceLastTick > STALE_LIMIT_SEC
                sampledPrice = single(NaN); % NaN Cascade
            else
                sampledPrice = latestPrice; 
            end

            % 3. EXECUTE ENGINE & EMIT SIGNAL
            [targetPosition, confidence] = calculateSignal(sampledPrice);

            emitTimestamp = uint32(posixtime(datetime('now','TimeZone','local')));

            outBuffer = [ ...
                typecast(TARGET_ID, 'uint8'), ...
                typecast(int32(targetPosition), 'uint8'), ...
                typecast(single(confidence), 'uint8'), ...
                typecast(emitTimestamp, 'uint8') ...   
                ];

            write(uOut, outBuffer, "uint8", PYTHON_HOST, SIGNAL_PORT);

            if targetPosition ~= 0
                fprintf('Signal generated -> Target: %d | Alpha: %.2f\n', targetPosition, confidence);
            end
        end

        % 1ms high-frequency poll loop 
        pause(0.001);
    end

catch err
    fprintf('Listener died: %s\n', err.message);
    clear uIn uOut;
end