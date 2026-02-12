import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Box, Newline, Text, useApp, useInput, useStdin} from 'ink';
import Spinner from 'ink-spinner';
import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import path from 'node:path';
import readline from 'node:readline';

type RunStatus = 'idle' | 'running' | 'done' | 'error';

type SimulationEvent = {
	contract_version: string;
	event: 'progress' | 'result' | 'error';
	payload: Record<string, unknown>;
};

type BestDesign = {
	combined_score: number;
	wing: {
		airfoil: string;
		wingspan_m: number;
		aspect_ratio: number;
		sweep_deg: number;
		dihedral_deg: number;
		root_incidence_deg: number;
		tip_incidence_deg: number;
		cg_fraction_mac: number;
		cruise_ld: number;
		stall_speed_kmh: number;
		static_margin: number;
	};
	propulsion: {
		motor_name: string;
		prop_name: string;
		battery_parallel: number;
		gross_mass_g: number;
		weighted_range_km: number;
		weighted_endurance_h: number;
		cruise_throttle: number;
	};
};

type ResultPayload = {
	best_design: BestDesign;
	iterations: Array<{iteration: number; best_combined_score: number; best_weighted_range_km: number}>;
	top_wing_candidates: Array<{airfoil: string; score: number; cruise_ld: number; stall_speed_kmh: number}>;
	top_propulsion_candidates: Array<{motor_name: string; prop_name: string; score: number; weighted_range_km: number}>;
	airfoil_comparison: Array<{
		airfoil: string;
		evaluated_count: number;
		feasible_count: number;
		best_score: number;
		best_ld: number;
		best_static_margin: number;
		best_stall_speed_kmh: number;
	}>;
	artifacts: {stl_file: string};
};

const formatNumber = (value: number, digits = 2): string => {
	if (!Number.isFinite(value)) {
		return 'n/a';
	}
	return value.toFixed(digits);
};

const MAX_CRUISE_OVERRIDE_KMH = 99;
const MIN_CRUISE_OVERRIDE_KMH = 30;

const ProgressBar: React.FC<{percent: number}> = ({percent}) => {
	const total = 34;
	const clamped = Math.max(0, Math.min(100, percent));
	const filled = Math.round((clamped / 100) * total);
	return (
		<Text color="cyanBright">
			[{`${'█'.repeat(filled)}${'░'.repeat(total - filled)}`}] {clamped.toFixed(0)}%
		</Text>
	);
};

const App: React.FC = () => {
	const {exit} = useApp();
	const {isRawModeSupported} = useStdin();
	const [status, setStatus] = useState<RunStatus>('idle');
	const [progressPercent, setProgressPercent] = useState<number>(0);
	const [stage, setStage] = useState<string>('boot');
	const [errorMessage, setErrorMessage] = useState<string | null>(null);
	const [result, setResult] = useState<ResultPayload | null>(null);
	const [logLines, setLogLines] = useState<string[]>([]);
	const [cruiseSpeedKmh, setCruiseSpeedKmh] = useState<number>(60);
	const [payloadWeightG, setPayloadWeightG] = useState<number>(0);
	const childRef = useRef<ChildProcessWithoutNullStreams | null>(null);
	const resultReceivedRef = useRef<boolean>(false);
	const overridesRef = useRef<{cruiseSpeedKmh: number; payloadWeightG: number}>({
		cruiseSpeedKmh: 60,
		payloadWeightG: 0,
	});

	const appendLog = useCallback((message: string): void => {
		setLogLines((prev) => [...prev.slice(-8), message]);
	}, []);

	const stopChild = useCallback(() => {
		if (childRef.current && !childRef.current.killed) {
			childRef.current.kill('SIGTERM');
		}
		childRef.current = null;
	}, []);

	const startRun = useCallback(() => {
		stopChild();
		setStatus('running');
		setProgressPercent(0);
		setStage('starting');
		setErrorMessage(null);
		setResult(null);
		resultReceivedRef.current = false;
		setLogLines([]);
		const {cruiseSpeedKmh: cruiseOverride, payloadWeightG: payloadOverride} = overridesRef.current;

		const repoRoot = path.resolve(process.cwd(), '../..');
		const child = spawn(
			'python3',
			[
				'scripts/simulate.py',
				'--override-mission-cruise-kmh',
				String(cruiseOverride),
				'--override-payload-g',
				String(payloadOverride),
			],
			{
			cwd: repoRoot,
			env: process.env,
			stdio: ['pipe', 'pipe', 'pipe'],
			},
		);

		childRef.current = child;
		appendLog(
			`Spawned backend simulation process (cruise=${formatNumber(cruiseOverride, 1)} km/h, payload=${formatNumber(payloadOverride, 0)} g).`,
		);

		const stdoutRl = readline.createInterface({input: child.stdout});
		stdoutRl.on('line', (line: string) => {
			let event: SimulationEvent;
			try {
				event = JSON.parse(line) as SimulationEvent;
			} catch {
				appendLog(`Non-JSON output: ${line}`);
				return;
			}

			if (event.event === 'progress') {
				const payload = event.payload;
				const percent = Number(payload.percent ?? 0);
				setProgressPercent(Number.isFinite(percent) ? percent : 0);
				setStage(String(payload.stage ?? 'unknown'));
				appendLog(`Progress: ${String(payload.stage)} (${percent}%)`);
			}

			if (event.event === 'result') {
				setResult(event.payload as unknown as ResultPayload);
				resultReceivedRef.current = true;
				setStatus('done');
				setProgressPercent(100);
				setStage('complete');
				appendLog('Optimization completed successfully.');
			}

			if (event.event === 'error') {
				const message = String(event.payload.message ?? 'Unknown backend error');
				setErrorMessage(message);
				setStatus('error');
				appendLog(`Error: ${message}`);
			}
		});

		child.stderr.on('data', (chunk: Buffer) => {
			appendLog(`stderr: ${chunk.toString().trim()}`);
		});

		child.on('close', (code: number | null) => {
			if (code !== 0 && !resultReceivedRef.current) {
				setStatus('error');
				setErrorMessage(`Backend exited with code ${String(code)}`);
			}
			appendLog(`Backend process exited (code ${String(code)}).`);
			childRef.current = null;
		});
	}, [appendLog, stopChild]);

	useEffect(() => {
		overridesRef.current = {cruiseSpeedKmh, payloadWeightG};
	}, [cruiseSpeedKmh, payloadWeightG]);

	useEffect(() => {
		startRun();
		return () => stopChild();
	}, [startRun, stopChild]);

	useInput(
		(input, key) => {
			if (input === 'q') {
				stopChild();
				exit();
			}
			if (input === 'r' && status !== 'running') {
				startRun();
			}
			if (key.return && status !== 'running') {
				startRun();
			}
			if (key.upArrow && status !== 'running') {
				setCruiseSpeedKmh((prev) => Math.min(prev + 2, MAX_CRUISE_OVERRIDE_KMH));
			}
			if (key.downArrow && status !== 'running') {
				setCruiseSpeedKmh((prev) => Math.max(prev - 2, MIN_CRUISE_OVERRIDE_KMH));
			}
			if (key.rightArrow && status !== 'running') {
				setPayloadWeightG((prev) => Math.min(prev + 25, 1000));
			}
			if (key.leftArrow && status !== 'running') {
				setPayloadWeightG((prev) => Math.max(prev - 25, 0));
			}
		},
		{isActive: Boolean(isRawModeSupported)},
	);

	const statusColor = useMemo(() => {
		switch (status) {
			case 'running':
				return 'yellowBright';
			case 'done':
				return 'greenBright';
			case 'error':
				return 'redBright';
			default:
				return 'gray';
		}
	}, [status]);

	return (
		<Box flexDirection="column" paddingX={1} paddingY={1}>
			<Box borderStyle="round" borderColor="cyanBright" flexDirection="column" paddingX={1}>
				<Text color="cyanBright">WingGen Optimizer Terminal</Text>
				<Text color="gray">Ink UI · autonomous optimization runtime</Text>
			</Box>
			<Newline />
			<Box borderStyle="round" borderColor="yellow" flexDirection="column" paddingX={1}>
				<Box>
					<Text color={statusColor}>Status: {status.toUpperCase()}</Text>
					<Text> · Stage: {stage}</Text>
					{status === 'running' ? (
						<>
							<Text> · </Text>
							<Text color="yellowBright">
								<Spinner type="dots" /> Running
							</Text>
						</>
					) : null}
				</Box>
				<ProgressBar percent={progressPercent} />
			</Box>
			<Newline />
			{errorMessage ? (
				<Box borderStyle="round" borderColor="redBright" paddingX={1}>
					<Text color="redBright">{errorMessage}</Text>
				</Box>
			) : null}
			{result ? (
				<>
					<Box borderStyle="round" borderColor="greenBright" flexDirection="column" paddingX={1}>
						<Text color="greenBright">Best Integrated Design</Text>
						<Text>
							Wing: {result.best_design.wing.airfoil.toUpperCase()} · span {formatNumber(result.best_design.wing.wingspan_m)} m · AR {formatNumber(result.best_design.wing.aspect_ratio)}
						</Text>
						<Text>
							Geometry: sweep {formatNumber(result.best_design.wing.sweep_deg)} deg · dihedral {formatNumber(result.best_design.wing.dihedral_deg)} deg
						</Text>
						<Text>
							Incidence: root {formatNumber(result.best_design.wing.root_incidence_deg)} deg · tip {formatNumber(result.best_design.wing.tip_incidence_deg)} deg · CG {formatNumber(result.best_design.wing.cg_fraction_mac * 100, 1)}% MAC
						</Text>
						<Text>
							Aero: L/D {formatNumber(result.best_design.wing.cruise_ld)} · stall {formatNumber(result.best_design.wing.stall_speed_kmh)} km/h · SM {formatNumber(result.best_design.wing.static_margin * 100)}%
						</Text>
						<Text>
							Propulsion: {result.best_design.propulsion.motor_name} + {result.best_design.propulsion.prop_name} · {result.best_design.propulsion.battery_parallel}P
						</Text>
						<Text>
							Performance: range {formatNumber(result.best_design.propulsion.weighted_range_km)} km · endurance {formatNumber(result.best_design.propulsion.weighted_endurance_h)} h · throttle {formatNumber(result.best_design.propulsion.cruise_throttle * 100)}%
						</Text>
						<Text>STL: {result.artifacts.stl_file}</Text>
					</Box>
					<Newline />
					<Box borderStyle="round" borderColor="blueBright" flexDirection="column" paddingX={1}>
						<Text color="blueBright">Top Candidates</Text>
						<Text>
							Wings: {result.top_wing_candidates.map((w) => `${w.airfoil}:${formatNumber(w.score, 1)}`).join(' | ')}
						</Text>
						<Text>
							Props: {result.top_propulsion_candidates.map((p) => `${p.motor_name}/${p.prop_name}:${formatNumber(p.score, 1)}`).join(' | ')}
						</Text>
					</Box>
					<Newline />
					<Box borderStyle="round" borderColor="cyan" flexDirection="column" paddingX={1}>
						<Text color="cyan">Airfoil Comparison</Text>
						{result.airfoil_comparison.map((item) => (
							<Text key={item.airfoil}>
								{item.airfoil}: eval {item.evaluated_count}, feasible {item.feasible_count}, L/D {formatNumber(item.best_ld, 2)}, SM {formatNumber(item.best_static_margin * 100, 1)}%, stall {formatNumber(item.best_stall_speed_kmh, 1)} km/h
							</Text>
						))}
					</Box>
				</>
			) : null}
			<Newline />
			<Box borderStyle="round" borderColor="gray" flexDirection="column" paddingX={1}>
				<Text color="gray">Recent Logs</Text>
				{logLines.length === 0 ? <Text color="gray">(none)</Text> : null}
				{logLines.map((line, index) => (
					<Text key={`${index}-${line}`}>{line}</Text>
				))}
			</Box>
			<Newline />
			<Box borderStyle="round" borderColor="magentaBright" flexDirection="column" paddingX={1}>
				<Text color="magentaBright">Interactive Controls</Text>
				<Text>
					Cruise Speed: {formatNumber(cruiseSpeedKmh, 1)} km/h · Payload: {formatNumber(payloadWeightG, 0)} g
				</Text>
				<Text color="gray">
					Cruise override range: {MIN_CRUISE_OVERRIDE_KMH}-{MAX_CRUISE_OVERRIDE_KMH} km/h
				</Text>
				<Text>
					{isRawModeSupported
						? 'Arrows: adjust inputs · Enter: run · r: rerun · q: quit'
						: 'Raw keyboard input unavailable in this terminal session'}
				</Text>
			</Box>
			<Newline />
			<Text color="gray">
				{isRawModeSupported
					? 'Tip: use ./start.sh for fully interactive mode in terminal'
					: 'Running in non-interactive mode (raw keyboard input unavailable)'}
			</Text>
		</Box>
	);
};

import {render} from 'ink';

render(<App />);
