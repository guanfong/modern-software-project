import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { FiMic, FiStopCircle, FiUpload, FiCheckCircle, FiAlertCircle, FiMessageSquare, FiEdit3, FiSave } from 'react-icons/fi';

interface InterviewHelperProps {
  roleId: string;
  candidateId: string;
  candidate: any;
  jd: any;
  briefing: any;
  role: any;
  onInterviewComplete?: () => void;
}

interface RequiredField {
  field: string;
  collected: boolean;
  value?: string;
}

interface InterviewGuidance {
  missing_fields: string[];
  suggested_questions: string[];
  behavioral_probes: string[];
  technical_probes: string[];
  fitment_notes: string[];
}

export default function InterviewHelper({ roleId, candidateId, candidate, jd, briefing, role, onInterviewComplete }: InterviewHelperProps) {
  const [isRecording, setIsRecording] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [interview, setInterview] = useState<any>(null);
  const [guidance, setGuidance] = useState<InterviewGuidance | null>(null);
  const [requiredFields, setRequiredFields] = useState<RequiredField[]>([]);
  const [transcription, setTranscription] = useState<string>('');
  const [interviewLoading, setInterviewLoading] = useState(true);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [manualSummary, setManualSummary] = useState('');
  const [manualTranscription, setManualTranscription] = useState('');
  const [manualFitScore, setManualFitScore] = useState<number | ''>('');
  const [manualStrengths, setManualStrengths] = useState('');
  const [manualConcerns, setManualConcerns] = useState('');
  const [manualRecommendation, setManualRecommendation] = useState<string>('maybe');
  const [manualResponses, setManualResponses] = useState<Record<string, string>>({});
  const [savingManual, setSavingManual] = useState(false);
  const [showManualForm, setShowManualForm] = useState(true);
  const [togglingCompleted, setTogglingCompleted] = useState(false);

  const hasValidIds = roleId && candidateId;

  useEffect(() => {
    if (!hasValidIds) return;
    setInterviewLoading(true);
    fetchInterview();
    // Initialize required fields from role's candidate_requirement_fields
    if (role?.candidate_requirement_fields && Array.isArray(role.candidate_requirement_fields)) {
      const fields = role.candidate_requirement_fields.map((field: string) => ({
        field: field,
        collected: false,
      }));
      setRequiredFields(fields);
    }
    // Don't fetch guidance automatically - only when user starts recording or uploads audio
  }, [candidateId, roleId, role]);

  const fetchInterview = async () => {
    if (!roleId || !candidateId) return;
    try {
      const response = await axios.get(`/api/roles/${roleId}/candidates/${candidateId}/interview`);
      if (response.data.interview) {
        const inv = response.data.interview;
        setInterview(inv);
        updateRequiredFields(inv.candidate_responses || {});
        setManualSummary(inv.summary || '');
        setManualTranscription(inv.transcription || '');
        setManualFitScore(inv.fit_score ?? '');
        setManualStrengths(Array.isArray(inv.strengths) ? inv.strengths.join('\n') : (inv.strengths || ''));
        setManualConcerns(Array.isArray(inv.concerns) ? inv.concerns.join('\n') : (inv.concerns || ''));
        setManualRecommendation(inv.recommendation || 'maybe');
        setManualResponses(inv.candidate_responses || {});
      } else {
        setInterview(null);
        setManualSummary('');
        setManualTranscription('');
        setManualFitScore('');
        setManualStrengths('');
        setManualConcerns('');
        setManualRecommendation('maybe');
        setManualResponses({});
      }
    } catch (error) {
      setInterview(null);
      setManualSummary('');
      setManualTranscription('');
      setManualFitScore('');
      setManualStrengths('');
      setManualConcerns('');
      setManualRecommendation('maybe');
      setManualResponses({});
    } finally {
      setInterviewLoading(false);
    }
  };

  const fetchGuidance = async () => {
    try {
      const response = await axios.post(`/api/roles/${roleId}/candidates/${candidateId}/interview/guidance`, {
        candidate: candidate,
        jd: jd,
        briefing: briefing,
        current_transcription: transcription,
      });
      setGuidance(response.data.guidance);
    } catch (error) {
      console.error('Error fetching guidance:', error);
    }
  };

  const updateRequiredFields = (responses: any) => {
    setRequiredFields(prev => prev.map(field => ({
      ...field,
      collected: !!responses[field.field],
      value: responses[field.field] || undefined,
    })));
  };

  const handleStartRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        if (chunksRef.current.length === 0) return;
        const blob = new Blob(chunksRef.current, { type: mimeType });
        const file = new File([blob], 'recording.webm', { type: blob.type });
        setIsUploading(true);
        try {
          const formData = new FormData();
          formData.append('file', file);
          const response = await axios.post(`/api/roles/${roleId}/candidates/${candidateId}/interview`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
          });
          setInterview(response.data.interview);
          updateRequiredFields(response.data.interview.candidate_responses || {});
          onInterviewComplete?.();
          await fetchGuidance();
          alert('Recording uploaded and processed successfully!');
        } catch (err: any) {
          console.error('Error uploading recording:', err);
          alert(err.response?.data?.detail || 'Failed to process recording.');
        } finally {
          setIsUploading(false);
        }
      };

      recorder.start(1000);
      setIsRecording(true);
      await fetchGuidance();
    } catch (err: any) {
      console.error('Error starting recording:', err);
      alert(err?.message?.includes('Permission') ? 'Microphone access is required to record.' : (err?.message || 'Could not start recording.'));
    }
  };

  const handleStopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current = null;
      setIsRecording(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await axios.post(`/api/roles/${roleId}/candidates/${candidateId}/interview`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setInterview(response.data.interview);
      updateRequiredFields(response.data.interview.candidate_responses || {});
      onInterviewComplete?.();
      // Fetch guidance after interview is processed
      await fetchGuidance();
      alert('Interview uploaded and processed successfully!');
    } catch (error: any) {
      console.error('Error uploading interview:', error);
      alert(`Error: ${error.response?.data?.detail || 'Failed to upload interview'}`);
    } finally {
      setIsUploading(false);
      e.target.value = '';
    }
  };

  const handleGetGuidance = async () => {
    await fetchGuidance();
  };

  const handleSetInterviewCompleted = async (completed: boolean) => {
    setTogglingCompleted(true);
    try {
      const { data } = await axios.put(`/api/roles/${roleId}/candidates/${candidateId}/interview`, {
        interview_completed: completed,
      });
      setInterview(data.interview);
      onInterviewComplete?.();
    } catch (err: any) {
      console.error('Error updating completed status:', err);
      alert(err.response?.data?.detail || 'Failed to update status');
    } finally {
      setTogglingCompleted(false);
    }
  };

  const handleSaveManualInterview = async () => {
    setSavingManual(true);
    try {
      const strengthsList = manualStrengths.trim() ? manualStrengths.trim().split('\n').map(s => s.trim()).filter(Boolean) : [];
      const concernsList = manualConcerns.trim() ? manualConcerns.trim().split('\n').map(s => s.trim()).filter(Boolean) : [];
      const payload = {
        summary: manualSummary.trim() || undefined,
        transcription: manualTranscription.trim() || undefined,
        candidate_responses: Object.keys(manualResponses).length ? manualResponses : undefined,
        fit_score: manualFitScore === '' ? undefined : Number(manualFitScore),
        strengths: strengthsList.length ? strengthsList : undefined,
        concerns: concernsList.length ? concernsList : undefined,
        recommendation: manualRecommendation,
        interview_completed: true,
      };
      const { data } = await axios.put(`/api/roles/${roleId}/candidates/${candidateId}/interview`, payload);
      setInterview(data.interview);
      updateRequiredFields(data.interview.candidate_responses || {});
      onInterviewComplete?.();
      setShowManualForm(false);
      alert('Interview saved and marked as completed. Candidate will now appear in Evaluation.');
    } catch (err: any) {
      console.error('Error saving manual interview:', err);
      alert(err.response?.data?.detail || 'Failed to save interview');
    } finally {
      setSavingManual(false);
    }
  };

  if (!hasValidIds) {
    return (
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-6 text-center text-amber-800">
        <p>Select a candidate above to view or add interview details.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {interviewLoading && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-center text-blue-800 text-sm">
          Loading interview data…
        </div>
      )}
      {/* Interview Evaluation - Show first when interview exists */}
      {interview && (
        <div className="bg-white rounded-lg shadow p-6 border-2 border-green-200">
          <h2 className="text-xl font-semibold mb-4 flex items-center gap-2">
            <span className="w-2 h-2 bg-green-500 rounded-full" />
            Completed Interview Evaluation
          </h2>
          <div className="mb-4 p-3 bg-gray-50 rounded-lg flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium text-gray-700">
              Interview status:{' '}
              {interview.interview_completed !== false ? (
                <span className="text-green-700">Completed (eligible for Evaluation)</span>
              ) : (
                <span className="text-amber-700">Not completed</span>
              )}
            </span>
            <div className="flex gap-2">
              {interview.interview_completed !== false ? (
                <button
                  type="button"
                  onClick={() => handleSetInterviewCompleted(false)}
                  disabled={togglingCompleted}
                  className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  {togglingCompleted ? 'Updating…' : 'Unmark completed'}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleSetInterviewCompleted(true)}
                  disabled={togglingCompleted}
                  className="px-3 py-1.5 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
                >
                  {togglingCompleted ? 'Updating…' : 'Mark as completed'}
                </button>
              )}
            </div>
          </div>
          <div className="space-y-4">
            <div>
              <h3 className="font-semibold text-gray-900 mb-2">Summary</h3>
              <p className="text-gray-700">{interview.summary || 'N/A'}</p>
            </div>

            {interview.fit_score !== undefined && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Fit Score</h3>
                <div className="flex items-center gap-4">
                  <div className="flex-1 bg-gray-200 rounded-full h-4">
                    <div
                      className={`h-4 rounded-full ${
                        interview.fit_score >= 70 ? 'bg-green-600' :
                        interview.fit_score >= 50 ? 'bg-yellow-600' : 'bg-red-600'
                      }`}
                      style={{ width: `${interview.fit_score}%` }}
                    ></div>
                  </div>
                  <span className="font-semibold text-gray-900">{interview.fit_score}/100</span>
                </div>
              </div>
            )}

            {interview.strengths && interview.strengths.length > 0 && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Strengths</h3>
                <ul className="list-disc list-inside text-gray-700">
                  {interview.strengths.map((strength: string, i: number) => (
                    <li key={i}>{strength}</li>
                  ))}
                </ul>
              </div>
            )}

            {interview.concerns && interview.concerns.length > 0 && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Concerns</h3>
                <ul className="list-disc list-inside text-gray-700">
                  {interview.concerns.map((concern: string, i: number) => (
                    <li key={i}>{concern}</li>
                  ))}
                </ul>
              </div>
            )}

            {interview.recommendation && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Recommendation</h3>
                <span className={`inline-block px-4 py-2 rounded-lg font-semibold ${
                  interview.recommendation === 'yes' ? 'bg-green-100 text-green-800' :
                  interview.recommendation === 'no' ? 'bg-red-100 text-red-800' :
                  'bg-yellow-100 text-yellow-800'
                }`}>
                  {interview.recommendation.toUpperCase()}
                </span>
              </div>
            )}

            {interview.candidate_responses && Object.keys(interview.candidate_responses).length > 0 && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Candidate Responses</h3>
                <div className="bg-gray-50 rounded-lg p-4 space-y-2">
                  {Object.entries(interview.candidate_responses).map(([key, value]) => (
                    <div key={key}>
                      <span className="font-medium text-gray-700">
                        {key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}:
                      </span>{' '}
                      <span className="text-gray-600">{value as string}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Interview Recording Section */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">Interview Recording</h2>
        <div className="flex gap-4 mb-4">
          <button
            onClick={isRecording ? handleStopRecording : handleStartRecording}
            disabled={isUploading}
            className={`flex items-center gap-2 px-6 py-3 rounded-lg ${
              isRecording
                ? 'bg-red-600 text-white hover:bg-red-700'
                : 'bg-blue-600 text-white hover:bg-blue-700'
            } disabled:opacity-50`}
          >
            {isRecording ? (
              <>
                <FiStopCircle className="w-5 h-5" />
                Stop Recording
              </>
            ) : (
              <>
                <FiMic className="w-5 h-5" />
                Start Recording
              </>
            )}
          </button>

          <label className="flex items-center gap-2 bg-gray-600 text-white px-6 py-3 rounded-lg hover:bg-gray-700 cursor-pointer">
            <FiUpload className="w-5 h-5" />
            {isUploading ? 'Uploading...' : 'Upload Audio File'}
            <input
              type="file"
              accept="audio/*"
              onChange={handleFileUpload}
              className="hidden"
              disabled={isUploading}
            />
          </label>
        </div>

        {isRecording && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4">
            <div className="flex items-center gap-2 text-red-600">
              <div className="w-3 h-3 bg-red-600 rounded-full animate-pulse"></div>
              <span className="font-semibold">Recording in progress...</span>
            </div>
          </div>
        )}
      </div>

      {/* Manual interview: add details & mark as completed (no recording) */}
      <div className="bg-white rounded-lg shadow p-6">
        <button
          type="button"
          onClick={() => setShowManualForm(!showManualForm)}
          className="flex items-center gap-2 text-left w-full mb-2"
        >
          <FiEdit3 className="w-5 h-5 text-gray-600" />
          <h2 className="text-xl font-semibold">
            {interview ? 'Edit interview details' : 'Add interview manually (mark as completed)'}
          </h2>
        </button>
        <p className="text-sm text-gray-500 mb-4">
          Not all interviews are recorded. Add notes and mark as completed so this candidate appears in Evaluation.
        </p>
        {showManualForm && (
          <div className="space-y-4 pt-4 border-t border-gray-200">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Summary</label>
              <textarea
                value={manualSummary}
                onChange={(e) => setManualSummary(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2"
                rows={3}
                placeholder="Brief summary of the interview"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Transcription / notes (optional)</label>
              <textarea
                value={manualTranscription}
                onChange={(e) => setManualTranscription(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2"
                rows={4}
                placeholder="Paste or type interview notes"
              />
            </div>
            {(role?.candidate_requirement_fields && role.candidate_requirement_fields.length > 0) && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Candidate responses (required fields)</label>
                <div className="space-y-2">
                  {role.candidate_requirement_fields.map((field: string) => (
                    <div key={field}>
                      <label className="block text-xs text-gray-500 mb-0.5">
                        {field.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                      </label>
                      <input
                        type="text"
                        value={manualResponses[field] ?? ''}
                        onChange={(e) => setManualResponses(prev => ({ ...prev, [field]: e.target.value }))}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                        placeholder="Answer"
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Fit score (0–100)</label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={manualFitScore}
                  onChange={(e) => setManualFitScore(e.target.value === '' ? '' : parseInt(e.target.value, 10))}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Recommendation</label>
                <select
                  value={manualRecommendation}
                  onChange={(e) => setManualRecommendation(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2"
                >
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                  <option value="maybe">Maybe</option>
                </select>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Strengths (one per line)</label>
              <textarea
                value={manualStrengths}
                onChange={(e) => setManualStrengths(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2"
                rows={2}
                placeholder="Strength 1&#10;Strength 2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Concerns (one per line)</label>
              <textarea
                value={manualConcerns}
                onChange={(e) => setManualConcerns(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2"
                rows={2}
                placeholder="Concern 1&#10;Concern 2"
              />
            </div>
            <button
              type="button"
              onClick={handleSaveManualInterview}
              disabled={savingManual}
              className="flex items-center gap-2 bg-green-600 text-white px-6 py-3 rounded-lg hover:bg-green-700 disabled:opacity-50"
            >
              <FiSave className="w-5 h-5" />
              {savingManual ? 'Saving...' : 'Save & mark as completed'}
            </button>
          </div>
        )}
      </div>

      {/* Real-Time Guidance Section */}
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-semibold">Interview Guidance</h2>
          <button
            onClick={handleGetGuidance}
            className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 text-sm"
          >
            <FiMessageSquare className="w-4 h-4" />
            Get Guidance
          </button>
        </div>

        {guidance ? (
          <div className="space-y-4">
            {/* Missing Fields */}
            {guidance.missing_fields && guidance.missing_fields.length > 0 && (
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <h3 className="font-semibold text-yellow-900 mb-2 flex items-center gap-2">
                  <FiAlertCircle className="w-5 h-5" />
                  Required Fields Not Yet Collected
                </h3>
                <ul className="list-disc list-inside text-sm text-yellow-800">
                  {guidance.missing_fields.map((field, i) => (
                    <li key={i}>{field}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Suggested Questions */}
            {guidance.suggested_questions && guidance.suggested_questions.length > 0 && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Suggested Follow-Up Questions</h3>
                <div className="space-y-2">
                  {guidance.suggested_questions.map((question, i) => (
                    <div key={i} className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                      <p className="text-sm text-gray-700">{question}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Behavioral Probes */}
            {guidance.behavioral_probes && guidance.behavioral_probes.length > 0 && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Behavioral Probes</h3>
                <div className="space-y-2">
                  {guidance.behavioral_probes.map((probe, i) => (
                    <div key={i} className="bg-purple-50 border border-purple-200 rounded-lg p-3">
                      <p className="text-sm text-gray-700">{probe}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Technical Probes */}
            {guidance.technical_probes && guidance.technical_probes.length > 0 && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Technical Probes</h3>
                <div className="space-y-2">
                  {guidance.technical_probes.map((probe, i) => (
                    <div key={i} className="bg-green-50 border border-green-200 rounded-lg p-3">
                      <p className="text-sm text-gray-700">{probe}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Fitment Notes */}
            {guidance.fitment_notes && guidance.fitment_notes.length > 0 && (
              <div>
                <h3 className="font-semibold text-gray-900 mb-2">Fitment Analysis Notes</h3>
                <div className="space-y-2">
                  {guidance.fitment_notes.map((note, i) => (
                    <div key={i} className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                      <p className="text-sm text-gray-700">{note}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-center py-8 text-gray-500">
            <p>
              Click <span className="whitespace-nowrap">&ldquo;Get Guidance&rdquo;</span> to receive AI-powered interview
              suggestions
            </p>
          </div>
        )}
      </div>

      {/* Required Fields Tracker */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-xl font-semibold mb-4">Required Information Checklist</h2>
        <div className="space-y-2">
          {requiredFields.map((field, i) => (
            <div
              key={i}
              className={`flex items-center gap-3 p-3 rounded-lg ${
                field.collected ? 'bg-green-50 border border-green-200' : 'bg-gray-50 border border-gray-200'
              }`}
            >
              {field.collected ? (
                <FiCheckCircle className="w-5 h-5 text-green-600" />
              ) : (
                <div className="w-5 h-5 border-2 border-gray-300 rounded"></div>
              )}
              <div className="flex-1">
                <p className={`font-medium ${field.collected ? 'text-green-900' : 'text-gray-900'}`}>
                  {field.field.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                </p>
                {field.value && (
                  <p className="text-sm text-gray-600 mt-1">Answer: {field.value}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

    </div>
  );
}

