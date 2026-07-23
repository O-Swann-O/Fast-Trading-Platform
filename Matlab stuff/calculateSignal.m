function [targetPositions, confidences] = calculateSignal(conIds, prices)
    n        = numel(conIds);
    LOOKBACK = 600;

    persistent KEYS BUF HEAD COUNT
    if isempty(KEYS) || numel(KEYS) ~= n || any(KEYS(:) ~= uint32(conIds(:)))
        KEYS  = uint32(conIds(:));
        BUF   = nan(n, LOOKBACK, 'single');
        HEAD  = 1;
        COUNT = 0;
    end

    BUF(:, HEAD) = single(prices(:));
    HEAD  = mod(HEAD, LOOKBACK) + 1;
    COUNT = min(COUNT + 1, LOOKBACK);

    targetPositions = zeros(n, 1, 'int32');
    confidences     = zeros(n, 1, 'single');
end