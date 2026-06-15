function [targetPosition, confidence] = calculateSignal(newPrice)
    %#codegen
    ringbuffer_length = 10000;
    persistent ringBuffer head;

    if isempty(ringBuffer)
        ringBuffer = nan(1, ringbuffer_length, 'single');
        head = int32(1);
    end

    ringBuffer(head) = newPrice;
    head = mod(head, int32(ringbuffer_length)) + 1;

    targetPosition = int32(0);
    confidence = single(0.0);
end