import CachedIcon from "@mui/icons-material/Cached";
import CancelIcon from "@mui/icons-material/Cancel";
import {
    Alert,
    Autocomplete,
    Button,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    LinearProgress,
    TextField as MuiTextField,
    Stack,
    Typography,
} from "@mui/material";
import { Fragment, useEffect, useRef, useState } from "react";
import {
    ChipField,
    Datagrid,
    ListContextProvider,
    ResourceContextProvider,
    TextField,
    useGetList,
    useList,
    useNotify,
} from "react-admin";

import { SeverityField } from "../commons/custom_fields/SeverityField";
import SmallButton from "../commons/custom_fields/SmallButton";
import { Spinner } from "../commons/custom_fields/Spinner";
import { httpClient } from "../commons/ra-data-django-rest-framework";
import { getSettingListSize } from "../commons/user_settings/functions";
import ObservationExpand from "../core/observations/ObservationExpand";

interface RuleSimulationProps {
    rule: any;
    product?: any;
}

interface Simulation {
    id: string;
    status: "Queued" | "Running" | "Completed" | "Failed" | "Cancelled";
    candidate_count: number;
    processed_count: number;
    match_count: number;
    results: any[];
    error_message: string;
}

const ACTIVE_STATUSES = ["Queued", "Running"];

const RuleSimulation = ({ rule, product }: RuleSimulationProps) => {
    const dialogRef = useRef<HTMLDivElement>(null);
    const [open, setOpen] = useState(false);
    const notify = useNotify();
    const [data, setData] = useState<any[]>([]);
    const [count, setCount] = useState(0);
    const [loading, setLoading] = useState(false);
    const [simulation, setSimulation] = useState<Simulation | null>(null);
    const [selectedParser, setSelectedParser] = useState<any | null>(null);
    const [selectedProducts, setSelectedProducts] = useState<any[]>([]);
    const [scannerPrefix, setScannerPrefix] = useState(rule.scanner_prefix ?? "");
    const isGeneralRule = product === undefined;
    const simulationActive = simulation !== null && ACTIVE_STATUSES.includes(simulation.status);

    const { data: parsers = [] } = useGetList(
        "parsers",
        {
            pagination: { page: 1, perPage: 1000 },
            sort: { field: "name", order: "ASC" },
        },
        { enabled: open && isGeneralRule && simulation === null }
    );
    const { data: products = [] } = useGetList(
        "product_names",
        {
            pagination: { page: 1, perPage: 1000 },
            sort: { field: "name", order: "ASC" },
        },
        { enabled: open && isGeneralRule && simulation === null }
    );

    useEffect(() => {
        if (!open || !simulation?.id || !simulationActive) return;

        const interval = window.setInterval(() => {
            httpClient(window.__RUNTIME_CONFIG__.API_BASE_URL + "/rule_simulations/" + simulation.id + "/")
                .then((result: any) => {
                    const updatedSimulation = result.json as Simulation;
                    setSimulation(updatedSimulation);
                    if (updatedSimulation.status === "Completed") {
                        setCount(updatedSimulation.match_count);
                        setData(updatedSimulation.results);
                    }
                })
                .catch((error) => {
                    notify(error.message, { type: "warning" });
                });
        }, 1500);

        return () => window.clearInterval(interval);
    }, [notify, open, simulation?.id, simulationActive]);

    const startSimulation = () => {
        setLoading(true);
        const rulesProvider = isGeneralRule ? "general_rules" : "product_rules";
        const requestBody = {
            products: selectedProducts.map((selectedProduct) => selectedProduct.id),
            parser: selectedParser?.id ?? null,
            scanner_prefix: scannerPrefix,
        };

        httpClient(window.__RUNTIME_CONFIG__.API_BASE_URL + "/" + rulesProvider + "/" + rule.id + "/simulate/", {
            method: "POST",
            body: JSON.stringify(requestBody),
        })
            .then((result: any) => {
                setSimulation(result.json as Simulation);
                setLoading(false);
            })
            .catch((error) => {
                notify(error.message, { type: "warning" });
                setLoading(false);
            });
    };

    const cancelSimulation = () => {
        if (!simulation) return;
        setLoading(true);
        httpClient(window.__RUNTIME_CONFIG__.API_BASE_URL + "/rule_simulations/" + simulation.id + "/", {
            method: "DELETE",
        })
            .then(() => {
                setSimulation({ ...simulation, status: "Cancelled" });
                setLoading(false);
            })
            .catch((error) => {
                notify(error.message, { type: "warning" });
                setLoading(false);
            });
    };

    const handleOpen = () => {
        setOpen(true);
        setSimulation(null);
        setData([]);
        setCount(0);
        setSelectedParser(null);
        setSelectedProducts([]);
        setScannerPrefix(rule.scanner_prefix ?? "");
        localStorage.removeItem("RaStore.rule_simulation.datagrid.expanded");
    };

    const handleClose = (_event?: object, reason?: string) => {
        if (reason === "backdropClick" || simulationActive) return;
        setOpen(false);
    };

    const listContext = useList({ data });
    const progress = simulation?.candidate_count
        ? Math.min(100, (simulation.processed_count / simulation.candidate_count) * 100)
        : 0;

    return (
        <Fragment>
            <SmallButton title="Simulate" onClick={handleOpen} icon={<CachedIcon />} />
            <Dialog ref={dialogRef} open={open} onClose={handleClose} fullWidth maxWidth={"lg"}>
                <DialogTitle>Simulate rule {rule.name}</DialogTitle>
                <DialogContent>
                    {simulation === null && (
                        <Stack spacing={2} sx={{ marginTop: 1 }}>
                            <Typography>
                                Simulation runs in the background. Narrowing the scope reduces load and completes
                                faster.
                            </Typography>
                            {isGeneralRule && (
                                <Fragment>
                                    <Autocomplete
                                        multiple
                                        options={products}
                                        value={selectedProducts}
                                        getOptionLabel={(option: any) => option.name}
                                        isOptionEqualToValue={(option: any, value: any) => option.id === value.id}
                                        onChange={(_event, value) => setSelectedProducts(value)}
                                        renderInput={(params) => (
                                            <MuiTextField
                                                {...params}
                                                label="Products"
                                                helperText="Optional product scope"
                                            />
                                        )}
                                    />
                                    <Autocomplete
                                        options={parsers}
                                        value={selectedParser}
                                        getOptionLabel={(option: any) => option.name}
                                        isOptionEqualToValue={(option: any, value: any) => option.id === value.id}
                                        onChange={(_event, value) => setSelectedParser(value)}
                                        renderInput={(params) => (
                                            <MuiTextField
                                                {...params}
                                                label="Parser"
                                                helperText="Optional parser scope"
                                            />
                                        )}
                                    />
                                    <MuiTextField
                                        value={scannerPrefix}
                                        onChange={(event) => setScannerPrefix(event.target.value)}
                                        label="Scanner prefix"
                                        helperText="For example: Semgrep"
                                        slotProps={{ htmlInput: { maxLength: 255 } }}
                                    />
                                </Fragment>
                            )}
                        </Stack>
                    )}

                    {simulation && (
                        <Stack spacing={2} sx={{ marginTop: 1 }}>
                            <Typography>
                                Status: {simulation.status}. Processed {simulation.processed_count} of{" "}
                                {simulation.candidate_count} candidate observations.
                            </Typography>
                            {simulationActive && <LinearProgress variant="determinate" value={progress} />}
                            {simulation.status === "Failed" && (
                                <Alert severity="error">{simulation.error_message || "Rule simulation failed"}</Alert>
                            )}
                            {simulation.status === "Cancelled" && (
                                <Alert severity="info">Simulation was cancelled.</Alert>
                            )}
                        </Stack>
                    )}

                    {simulation?.status === "Completed" && (
                        <Fragment>
                            {count !== data.length && (
                                <Typography sx={{ marginTop: 2, marginBottom: 2 }}>
                                    Showing {data.length} of {count} observations.
                                </Typography>
                            )}
                            <ResourceContextProvider value="rule_simulation">
                                <ListContextProvider value={listContext}>
                                    <Datagrid
                                        data={data}
                                        total={count}
                                        isLoading={false}
                                        size={getSettingListSize()}
                                        bulkActionButtons={false}
                                        rowClick={false}
                                        expand={<ObservationExpand showComponent={true} />}
                                        expandSingle
                                    >
                                        <TextField source="title" label="Title" sortable={false} />
                                        {(product === undefined || product.is_product_group) && (
                                            <TextField source="product_data.name" label="Product" sortable={false} />
                                        )}
                                        <TextField source="branch_name" label="Branch / Version" sortable={false} />
                                        <SeverityField source="current_severity" label="Severity" />
                                        <ChipField source="current_status" label="Status" sortable={false} />
                                        <TextField source="scanner_name" label="Scanner" />
                                    </Datagrid>
                                </ListContextProvider>
                            </ResourceContextProvider>
                        </Fragment>
                    )}
                </DialogContent>
                <DialogActions>
                    {simulation === null && (
                        <Button
                            variant="contained"
                            onClick={startSimulation}
                            disabled={loading}
                            startIcon={<CachedIcon />}
                        >
                            Run simulation
                        </Button>
                    )}
                    {simulationActive && (
                        <Button color="error" onClick={cancelSimulation} disabled={loading} startIcon={<CancelIcon />}>
                            Cancel
                        </Button>
                    )}
                    {!simulationActive && simulation !== null && (
                        <Button variant="contained" onClick={() => handleClose()} color="inherit">
                            OK
                        </Button>
                    )}
                </DialogActions>
            </Dialog>
            <Spinner open={loading && open} />
        </Fragment>
    );
};

export default RuleSimulation;
