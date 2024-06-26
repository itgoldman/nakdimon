# from keras examples
# https://www.tensorflow.org/tutorials/text/transformer
import tensorflow as tf

import time
import numpy as np


def get_angles(pos, i, d_model):
    return pos / np.power(10000, (2 * (i//2)) / np.float32(d_model))


def positional_encoding(position, d_model):
    angle_rads = get_angles(np.arange(position)[:, np.newaxis],
                            np.arange(d_model)[np.newaxis, :],
                            d_model)
    
    # apply sin to even indices in the array; 2i
    angle_rads[:, 0::2] = np.sin(angle_rads[:, 0::2])
    
    # apply cos to odd indices in the array; 2i+1
    angle_rads[:, 1::2] = np.cos(angle_rads[:, 1::2])
        
    pos_encoding = angle_rads[np.newaxis, ...]
        
    return tf.cast(pos_encoding, dtype=tf.float32)


def create_look_ahead_mask(size):
    """create a diagonal matrix"""
    mask = 1 - tf.linalg.band_part(tf.ones((size, size)), -1, 0)
    # mask = tf.maximum(tf.eye(size), mask)
    return mask  # (seq_len, seq_len)
    # return tf.linalg.band_part(tf.ones((size, size)), tf.cast(0, size.dtype), size)
    # tf.constant(np.triu(np.ones(size)), dtype=tf.float32)
    # return 1 - tf.linalg.band_part(tf.ones((size, size)), -1, 0)  # (seq_len, seq_len)


def scaled_dot_product_attention(q, k, v, mask=None, causal=False):
    """Calculate the attention weights.
    q, k, v must have matching leading dimensions.
    k, v must have matching penultimate dimension, i.e.: seq_len_k = seq_len_v.
    The mask has different shapes depending on its type(padding or look ahead) 
    but it must be broadcastable for addition.
    
    Args:
        q: query shape == (..., seq_len_q, depth)
        k: key shape == (..., seq_len_k, depth)
        v: value shape == (..., seq_len_v, depth_v)
        mask: Float tensor with shape broadcastable 
            to (..., seq_len_q, seq_len_k). Defaults to None.
        
    Returns:
        output, attention_weights
    """
    matmul_qk = tf.matmul(q, k, transpose_b=True)  # (..., seq_len_q, seq_len_k)

    # scale matmul_qk
    dk = tf.cast(tf.shape(k)[-1], tf.float32)
    scaled_attention_logits = matmul_qk / tf.math.sqrt(dk)

    if causal:
        size = tf.shape(k)[-2]
        look_ahead_mask = create_look_ahead_mask(size)
        if mask is None:
            mask = look_ahead_mask
        else:
            mask = tf.maximum(mask, look_ahead_mask)

    if mask is not None:
        scaled_attention_logits += (mask * -1e17)
    
    attention_weights = tf.nn.softmax(scaled_attention_logits, axis=-1)  # (..., seq_len_q, seq_len_k)

    output = tf.matmul(attention_weights, v)  # (..., seq_len_q, depth_v)

    return output, attention_weights


class MultiHeadAttention(tf.keras.layers.Layer):
    def __init__(self, d_model, num_heads, causal=False):
        super().__init__()
        self.num_heads = num_heads
        self.d_model = d_model
        
        assert d_model % self.num_heads == 0
        
        self.depth = d_model // self.num_heads
        
        self.wq = tf.keras.layers.Dense(d_model)
        self.wk = tf.keras.layers.Dense(d_model)
        self.wv = tf.keras.layers.Dense(d_model)
        
        self.dense = tf.keras.layers.Dense(d_model)

        self.causal = causal
            
    def split_heads(self, x, batch_size):
        """Split the last dimension into (num_heads, depth).
        Transpose the result such that the shape is (batch_size, num_heads, seq_len, depth)
        """
        x = tf.reshape(x, (batch_size, -1, self.num_heads, self.depth))
        return tf.transpose(x, perm=[0, 2, 1, 3])
        
    def call(self, v, k, q, mask):
        batch_size = tf.shape(q)[0]
        
        q = self.wq(q)  # (batch_size, seq_len, d_model)
        k = self.wk(k)  # (batch_size, seq_len, d_model)
        v = self.wv(v)  # (batch_size, seq_len, d_model)
        
        q = self.split_heads(q, batch_size)  # (batch_size, num_heads, seq_len_q, depth)
        k = self.split_heads(k, batch_size)  # (batch_size, num_heads, seq_len_k, depth)
        v = self.split_heads(v, batch_size)  # (batch_size, num_heads, seq_len_v, depth)
        
        # scaled_attention.shape == (batch_size, num_heads, seq_len_q, depth)
        # attention_weights.shape == (batch_size, num_heads, seq_len_q, seq_len_k)
        scaled_attention, attention_weights = scaled_dot_product_attention(q, k, v, mask, self.causal)
        
        scaled_attention = tf.transpose(scaled_attention, perm=[0, 2, 1, 3])  # (batch_size, seq_len_q, num_heads, depth)

        concat_attention = tf.reshape(scaled_attention, (batch_size, -1, self.d_model))  # (batch_size, seq_len_q, d_model)

        output = self.dense(concat_attention)  # (batch_size, seq_len_q, d_model)
            
        return output, attention_weights


def pointwise_feed_forward_network(d_model, dff):
    return tf.keras.Sequential([
        tf.keras.layers.Dense(dff, activation='relu'),  # (batch_size, seq_len, dff)
        tf.keras.layers.Dense(d_model)                  # (batch_size, seq_len, d_model)
    ])


class EncoderLayer(tf.keras.layers.Layer):
    def __init__(self, d_model, num_heads, dff, rate=0.1):
        super().__init__()

        self.mha = MultiHeadAttention(d_model, num_heads)
        self.ffn = pointwise_feed_forward_network(d_model, dff)

        self.layernorm1 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm2 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        
        self.dropout1 = tf.keras.layers.Dropout(rate)
        self.dropout2 = tf.keras.layers.Dropout(rate)
        
    def call(self, x, training, mask):

        attn_output, _ = self.mha(x, x, x, mask)  # (batch_size, input_seq_len, d_model)
        attn_output = self.dropout1(attn_output, training=training)
        out1 = self.layernorm1(x + attn_output)  # (batch_size, input_seq_len, d_model)
        
        ffn_output = self.ffn(out1)  # (batch_size, input_seq_len, d_model)
        ffn_output = self.dropout2(ffn_output, training=training)
        out2 = self.layernorm2(out1 + ffn_output)  # (batch_size, input_seq_len, d_model)
        
        return out2


class Encoder(tf.keras.layers.Layer):
    def __init__(self, num_layers, d_model, num_heads, dff, input_vocab_size,
                maximum_position_encoding, rate=0.1):
        super().__init__()

        self.d_model = d_model
        self.num_layers = num_layers
        
        self.embedding = tf.keras.layers.Embedding(input_vocab_size, d_model)
        self.pos_encoding = positional_encoding(maximum_position_encoding, self.d_model)
        
        self.enc_layers = [EncoderLayer(d_model, num_heads, dff, rate) for _ in range(num_layers)]
    
        self.dropout = tf.keras.layers.Dropout(rate)
            
    def call(self, x, training, mask):

        seq_len = tf.shape(x)[1]
        
        # adding embedding and position encoding.
        x = self.embedding(x)  # (batch_size, input_seq_len, d_model)
        x *= tf.math.sqrt(tf.cast(self.d_model, tf.float32))
        x += self.pos_encoding[:, :seq_len, :]

        x = self.dropout(x, training=training)
        
        for i in range(self.num_layers):
            x = self.enc_layers[i](x, training, mask)
        
        return x  # (batch_size, input_seq_len, d_model)


class DecoderLayer(tf.keras.layers.Layer):
    def __init__(self, d_model, num_heads, dff, rate=0.1):
        super().__init__()

        self.mha1 = MultiHeadAttention(d_model, num_heads, causal=True)
        self.mha2 = MultiHeadAttention(d_model, num_heads, causal=False)

        self.ffn = pointwise_feed_forward_network(d_model, dff)
    
        self.layernorm1 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm2 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm3 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        
        self.dropout1 = tf.keras.layers.Dropout(rate)
        self.dropout2 = tf.keras.layers.Dropout(rate)
        self.dropout3 = tf.keras.layers.Dropout(rate)
        
        
    def call(self, y, enc_output, training, dec_target_padding_mask, padding_mask):
        # enc_output.shape == (batch_size, input_seq_len, d_model)

        attn1, attn_weights_block1 = self.mha1(y, y, y, dec_target_padding_mask)  # (batch_size, target_seq_len, d_model)
        
        attn1 = self.dropout1(attn1, training=training)
        out1 = self.layernorm1(attn1 + y)  #  ! attn1 + y is a leak?

        attn2, attn_weights_block2 = self.mha2(enc_output, enc_output, out1, padding_mask)  # (batch_size, target_seq_len, d_model)
        attn2 = self.dropout2(attn2, training=training)
        out2 = self.layernorm2(attn2 + out1)  # (batch_size, target_seq_len, d_model)

        ffn_output = self.ffn(out2)  # (batch_size, target_seq_len, d_model)
        ffn_output = self.dropout3(ffn_output, training=training)
        out3 = self.layernorm3(ffn_output + out2)  # (batch_size, target_seq_len, d_model)

        return out3, attn_weights_block1, attn_weights_block2


class Decoder(tf.keras.layers.Layer):
    def __init__(self, num_layers, d_model, num_heads, dff, sizes,
                 maximum_position_encoding, rate=0.1):
        super().__init__()

        self.d_model = d_model
        self.num_layers = num_layers
        
        self.embeddings = [tf.keras.layers.Embedding(size, d_model) for size in sizes]

        self.pos_encoding = positional_encoding(maximum_position_encoding, d_model)
        
        self.dec_layers = [DecoderLayer(d_model, num_heads, dff, rate) for _ in range(num_layers)]
        self.dropout = tf.keras.layers.Dropout(rate)
        
    def call(self, ys, enc_output, training, dec_target_padding_mask, padding_mask):

        seq_len = tf.shape(ys[0])[1]
        
        y = tf.keras.layers.add([emb(y) for emb, y in zip(self.embeddings, ys)]) # (batch_size, target_seq_len, d_model)
        y *= tf.math.sqrt(tf.cast(self.d_model, tf.float32))
        y += self.pos_encoding[:, :seq_len, :]
        
        y = self.dropout(y, training=training)

        attention_weights = {}
        for i in range(self.num_layers):
            y, block1, block2 = self.dec_layers[i](y, enc_output, training, dec_target_padding_mask, padding_mask)
            
            attention_weights['decoder_layer{}_block1'.format(i+1)] = block1
            attention_weights['decoder_layer{}_block2'.format(i+1)] = block2
        
        # x.shape == (batch_size, target_seq_len, d_model)
        return y, attention_weights


class Transformer(tf.keras.Model):
    def __init__(self, *, num_layers, d_model, num_heads, dff, input_vocab_size, 
                 output_sizes,
                 maximum_position_encoding_input, maximum_position_encoding_target, rate=0.1):
        super().__init__()

        self.encoder = Encoder(num_layers, d_model, num_heads, dff, 
                               input_vocab_size, maximum_position_encoding_input, rate)

        self.decoder = Decoder(num_layers, d_model, num_heads, dff, 
                               output_sizes, maximum_position_encoding_target, rate)

        self.masked_loss = MaskedCategoricalCrossentropy()

        self.softmax_dense_list = [(tf.keras.layers.Softmax(), tf.keras.layers.Dense(size)) for size in output_sizes]

        self.output_sizes = output_sizes

    def pseudo_build(self, input_maxlen, output_maxlen):
        # pseudo "build" step, to allow printing a summary:
        x = np.ones((2, input_maxlen), dtype=int)
        y_n = np.ones((2, output_maxlen), dtype=int)
        y_d = np.ones((2, output_maxlen), dtype=int)
        y_s = np.ones((2, output_maxlen), dtype=int)
        return self.train_step(x, y_n, y_d, y_s)

    def call(self, x, ys, training, dec_target_padding_mask, padding_mask):
        enc_output = self.encoder(x, training, padding_mask)  # (batch_size, x_seq_len, d_model)
        
        # dec_output.shape == (batch_size, y_seq_len, d_model)
        dec_output, attention_weights = self.decoder(ys, enc_output, training, dec_target_padding_mask, padding_mask)
        
        out = [softmax(dense(dec_output)) for softmax, dense in self.softmax_dense_list]
        return out, attention_weights

    train_step_signature = [
        tf.TensorSpec(shape=(None, None), dtype=tf.int64),
        tf.TensorSpec(shape=(None, None), dtype=tf.int64),
        tf.TensorSpec(shape=(None, None), dtype=tf.int64),
        tf.TensorSpec(shape=(None, None), dtype=tf.int64),
    ]

    @tf.function(input_signature=train_step_signature)
    def train_step(self, x, y_niqqud, y_dagesh, y_sin):
        # x: (batch_size, maxlen)
        ys = [y_niqqud, y_dagesh, y_sin]

        # add start token and remove last one
        y_inp = [tf.pad(y, [[0, 0], [1, 0]], "CONSTANT", constant_values=1)[:, :-1]
                 for y in ys]

        padding_mask = create_padding_mask(x)
        dec_target_padding_mask = create_padding_mask(y_inp[0])  # arbitrary

        with tf.GradientTape() as tape:
            y_preds, _ = self(x, y_inp, True, dec_target_padding_mask, padding_mask)
            loss = sum(self.compiled_loss(y, y_pred) for y, y_pred in zip(ys, y_preds))

        gradients = tape.gradient(loss, self.trainable_variables)    
        self.optimizer.apply_gradients(zip(gradients, self.trainable_variables))

        self.compiled_metrics.update_state(ys, y_preds)

        return {m.name: m.result() for m in self.metrics}

    # named "predict probs" instead of "predict" because tf does not allow to use that name
    # when `call` receives output as argument

    # @tf.function()
    def predict_probs(self, x):
        batch_len, timesteps = x.shape

        # we "know" that the first item is the start item
        y_probs = [make_start_prob(size, batch_len) for size in self.output_sizes]

        padding_mask = create_padding_mask(x)
        dec_target_padding_mask = create_padding_mask(x)
        
        invisible_future = tf.zeros([batch_len, timesteps], dtype=tf.int32)

        for i in range(timesteps):
            # Maybe this can be avoided by controlling the size of the output as in translation
            y_preds = [tf.concat([
                         tf.cast(tf.argmax(prob, axis=-1), tf.int32),
                         invisible_future[:, i+1:]
                       ], axis=-1) for prob in y_probs]
                
            predictions, _ = self(x, y_preds, False, dec_target_padding_mask, padding_mask)
            y_probs = [tf.concat([prob, pred[: ,i:i+1, :]], axis=1) for prob, pred in zip(y_probs, predictions)]

        # remove "start of" padding token
        y_probs = [prob[:, 1:, :] for prob in y_probs]
        return y_probs

    @tf.function()  # input_signature=train_step_signature)
    def test_step(self, x, y_niqqud, y_dagesh, y_sin, sample_weight=None):
        y_probs = self.predict_probs(x)

        ys = [y_niqqud, y_dagesh, y_sin]
        # Updates stateful loss metrics
        self.compiled_loss(ys, y_probs)
        self.compiled_metrics.update_state(ys, y_probs)

        return {f'val_{m.name}' : m.result() for m in self.metrics}

    def predict_argmax(self, x):
        y_probs = self.predict_probs(x)
        return [tf.cast(tf.argmax(y, axis=-1), tf.int32).numpy() for y in y_probs]


def make_start_prob(target_vocab_size, batch_len):
    y_probs = np.array([[[0] * target_vocab_size]] * batch_len)
    y_probs[:, 0, 1] = 1
    return tf.constant(y_probs, tf.float32)


class CustomSchedule(tf.keras.optimizers.schedules.LearningRateSchedule):
    def __init__(self, d_model, warmup_steps=4000):
        super().__init__()
        self.d_model = tf.cast(d_model, tf.float32)
        self.warmup_steps = warmup_steps
        
    def __call__(self, step):
        arg1 = tf.math.rsqrt(step)
        arg2 = step * (self.warmup_steps ** -1.5)
        
        return tf.math.rsqrt(self.d_model) * tf.math.minimum(arg1, arg2)


class MaskedCategoricalCrossentropy(tf.keras.losses.Loss):
    def __call__(self, y_true, y_pred, sample_weight=None):
        loss = tf.keras.losses.sparse_categorical_crossentropy(y_true, y_pred)

        mask = tf.cast(tf.math.logical_not(tf.math.equal(y_true, 0)), dtype=loss.dtype)
        loss *= mask

        return tf.reduce_sum(loss) / tf.reduce_sum(mask)


def masked_accuracy(real, pred):
    acc = tf.keras.metrics.sparse_categorical_accuracy(real, pred)

    mask = tf.cast(tf.math.logical_not(tf.math.equal(real, 0)), dtype=acc.dtype)
    acc *= mask

    return tf.reduce_sum(acc) / tf.reduce_sum(mask)

def masked_categorical_crossentropy(y_true, y_pred, sample_weight=None):
    loss = tf.keras.losses.sparse_categorical_crossentropy(y_true, y_pred)

    mask = tf.cast(tf.math.logical_not(tf.math.equal(y_true, 0)), dtype=loss.dtype)
    loss *= mask

    return tf.reduce_sum(loss) / tf.reduce_sum(mask)

def create_padding_mask(seq):
    seq = tf.cast(tf.math.equal(seq, 0), tf.float32)
    # add extra dimensions to add the padding to the attention logits.
    return seq[:, tf.newaxis, tf.newaxis, :]  # (batch_size, 1, 1, seq_len)
