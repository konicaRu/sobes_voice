// Передаёт в основной поток сэмплы микрофона блоками float32.
class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}

registerProcessor('recorder', RecorderProcessor);
