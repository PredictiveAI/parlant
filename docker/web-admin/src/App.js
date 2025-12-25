import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, TextInput, ScrollView, ActivityIndicator } from 'react-native';

const API_URLS = {
  parlant: process.env.REACT_APP_PARLANT_API_URL || 'http://localhost:8800',
  twilio: process.env.REACT_APP_TWILIO_SERVICE_URL || 'http://localhost:8003',
  tts: 'http://localhost:8001',
  stt: 'http://localhost:8002',
};

const StatusBadge = ({ status, label }) => {
  const isHealthy = status === 'healthy' || status === 'ok';
  return (
    <View style={[styles.badge, isHealthy ? styles.badgeHealthy : styles.badgeError]}>
      <Text style={styles.badgeText}>{label}: {status || 'Unknown'}</Text>
    </View>
  );
};

const ServiceCard = ({ title, status, details, loading }) => (
  <View style={styles.card}>
    <View style={styles.cardHeader}>
      <Text style={styles.cardTitle}>{title}</Text>
      {loading ? (
        <ActivityIndicator size="small" color="#4CAF50" />
      ) : (
        <View style={[styles.statusDot, status === 'healthy' ? styles.statusHealthy : styles.statusError]} />
      )}
    </View>
    {details && (
      <View style={styles.cardDetails}>
        {Object.entries(details).map(([key, value]) => (
          <Text key={key} style={styles.detailText}>
            {key}: {String(value)}
          </Text>
        ))}
      </View>
    )}
  </View>
);

const CallPanel = () => {
  const [phoneNumber, setPhoneNumber] = useState('');
  const [calling, setCalling] = useState(false);
  const [callResult, setCallResult] = useState(null);

  const initiateCall = async () => {
    if (!phoneNumber) return;

    setCalling(true);
    setCallResult(null);

    try {
      const response = await fetch(`${API_URLS.twilio}/api/calls/outbound`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to_number: phoneNumber }),
      });

      const result = await response.json();
      setCallResult(result);
    } catch (error) {
      setCallResult({ error: error.message });
    } finally {
      setCalling(false);
    }
  };

  return (
    <View style={styles.panel}>
      <Text style={styles.panelTitle}>Outbound Calls</Text>
      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          placeholder="Phone number (e.g., +1234567890)"
          placeholderTextColor="#666"
          value={phoneNumber}
          onChangeText={setPhoneNumber}
        />
        <TouchableOpacity
          style={[styles.button, calling && styles.buttonDisabled]}
          onPress={initiateCall}
          disabled={calling}
        >
          <Text style={styles.buttonText}>{calling ? 'Calling...' : 'Call'}</Text>
        </TouchableOpacity>
      </View>
      {callResult && (
        <View style={styles.result}>
          <Text style={styles.resultText}>
            {callResult.error ? `Error: ${callResult.error}` : `Call SID: ${callResult.call_sid}`}
          </Text>
        </View>
      )}
    </View>
  );
};

export default function App() {
  const [services, setServices] = useState({});
  const [loading, setLoading] = useState(true);

  const checkHealth = async (name, url) => {
    try {
      const healthUrl = name === 'parlant' ? `${url}/healthz` : `${url}/v1/health`;
      const response = await fetch(healthUrl);
      const data = await response.json();
      return { status: 'healthy', ...data };
    } catch (error) {
      return { status: 'error', error: error.message };
    }
  };

  const refreshServices = async () => {
    setLoading(true);
    const results = {};

    for (const [name, url] of Object.entries(API_URLS)) {
      results[name] = await checkHealth(name, url);
    }

    setServices(results);
    setLoading(false);
  };

  useEffect(() => {
    refreshServices();
    const interval = setInterval(refreshServices, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Parlant Voice Platform</Text>
        <Text style={styles.subtitle}>Admin Dashboard</Text>
      </View>

      <View style={styles.section}>
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Service Status</Text>
          <TouchableOpacity style={styles.refreshButton} onPress={refreshServices}>
            <Text style={styles.refreshText}>Refresh</Text>
          </TouchableOpacity>
        </View>

        <View style={styles.grid}>
          <ServiceCard
            title="Parlant API"
            status={services.parlant?.status}
            loading={loading}
          />
          <ServiceCard
            title="ChatTTS (TTS)"
            status={services.tts?.status}
            details={services.tts?.gpu_available ? { GPU: services.tts.gpu_name } : null}
            loading={loading}
          />
          <ServiceCard
            title="Whisper (STT)"
            status={services.stt?.status}
            details={services.stt?.model_name ? { Model: services.stt.model_name } : null}
            loading={loading}
          />
          <ServiceCard
            title="Twilio Integration"
            status={services.twilio?.status}
            details={services.twilio?.twilio_configured ? { Configured: 'Yes' } : null}
            loading={loading}
          />
        </View>
      </View>

      <View style={styles.section}>
        <CallPanel />
      </View>

      <View style={styles.footer}>
        <Text style={styles.footerText}>Parlant Voice Platform v1.0.0</Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1a1a2e',
  },
  header: {
    padding: 24,
    paddingTop: 48,
    backgroundColor: '#16213e',
    borderBottomWidth: 1,
    borderBottomColor: '#0f3460',
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#e94560',
  },
  subtitle: {
    fontSize: 16,
    color: '#888',
    marginTop: 4,
  },
  section: {
    padding: 16,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: '600',
    color: '#fff',
  },
  refreshButton: {
    backgroundColor: '#0f3460',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 6,
  },
  refreshText: {
    color: '#4CAF50',
    fontWeight: '500',
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 16,
  },
  card: {
    backgroundColor: '#16213e',
    borderRadius: 12,
    padding: 16,
    minWidth: 200,
    flex: 1,
    borderWidth: 1,
    borderColor: '#0f3460',
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#fff',
  },
  statusDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
  },
  statusHealthy: {
    backgroundColor: '#4CAF50',
  },
  statusError: {
    backgroundColor: '#f44336',
  },
  cardDetails: {
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#0f3460',
  },
  detailText: {
    fontSize: 12,
    color: '#888',
  },
  panel: {
    backgroundColor: '#16213e',
    borderRadius: 12,
    padding: 20,
    borderWidth: 1,
    borderColor: '#0f3460',
  },
  panelTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#fff',
    marginBottom: 16,
  },
  inputRow: {
    flexDirection: 'row',
    gap: 12,
  },
  input: {
    flex: 1,
    backgroundColor: '#1a1a2e',
    borderRadius: 8,
    padding: 12,
    color: '#fff',
    borderWidth: 1,
    borderColor: '#0f3460',
  },
  button: {
    backgroundColor: '#e94560',
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
    justifyContent: 'center',
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  buttonText: {
    color: '#fff',
    fontWeight: '600',
  },
  result: {
    marginTop: 16,
    padding: 12,
    backgroundColor: '#1a1a2e',
    borderRadius: 8,
  },
  resultText: {
    color: '#888',
    fontSize: 12,
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
  },
  badgeHealthy: {
    backgroundColor: '#1b5e20',
  },
  badgeError: {
    backgroundColor: '#b71c1c',
  },
  badgeText: {
    color: '#fff',
    fontSize: 12,
  },
  footer: {
    padding: 24,
    alignItems: 'center',
  },
  footerText: {
    color: '#666',
    fontSize: 12,
  },
});
