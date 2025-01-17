import Image from "@jy95/material-ui-image";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import CardMedia from "@mui/material/CardMedia";
import Grid from "@mui/material/Grid";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import LazyLoad from "react-lazyload";

import { SnapshotIcon } from "components/events/SnapshotEvent";
import {
  extractUniqueLabels,
  extractUniqueTypes,
  getEventTime,
  getEventTimestamp,
  getSrc,
} from "components/events/utils";
import VideoPlayerPlaceholder from "components/videoplayer/VideoPlayerPlaceholder";
import { getTimeFromDate } from "lib/helpers";
import * as types from "lib/types";

const getText = (events: types.CameraEvent[]) => {
  const uniqueEvents = extractUniqueTypes(events);
  return (
    <Box>
      <Typography variant="body2" align="center">{`${getTimeFromDate(
        new Date(getEventTime(events[0])),
      )}`}</Typography>
      <Grid container justifyContent="center" alignItems="center">
        {Object.keys(uniqueEvents).map((key) => {
          // For object detection we want to group by label
          if (key === "object") {
            const uniqueLabels = extractUniqueLabels(
              uniqueEvents[key] as Array<types.CameraObjectEvent>,
            );
            return Object.keys(uniqueLabels).map((label) => (
              <Grid item key={`icon-${key}-${label}`}>
                <SnapshotIcon events={uniqueLabels[label]} />
              </Grid>
            ));
          }
          return (
            <Grid item key={`icon-${key}`}>
              <SnapshotIcon events={uniqueEvents[key]} />
            </Grid>
          );
        })}
      </Grid>
    </Box>
  );
};

const isTimespanAvailable = (
  timestamp: number,
  availableTimespans: types.HlsAvailableTimespans,
) => {
  for (const timespan of availableTimespans.timespans) {
    if (timestamp >= timespan.start && timestamp <= timespan.end) {
      return true;
    }
  }
  return false;
};

type EventTableItemProps = {
  camera: types.Camera | types.FailedCamera;
  events: types.CameraEvent[];
  setSelectedEvent: (event: types.CameraEvent) => void;
  selected: boolean;
  setRequestedTimestamp: (timestamp: number | null) => void;
  availableTimespans: types.HlsAvailableTimespans;
};
export const EventTableItem = ({
  camera,
  events,
  setSelectedEvent,
  selected,
  setRequestedTimestamp,
  availableTimespans,
}: EventTableItemProps) => {
  const theme = useTheme();
  return (
    <Card
      variant="outlined"
      square
      sx={{
        border: selected
          ? `2px solid ${theme.palette.primary[400]}`
          : "2px solid transparent",
        borderRadius: "5px",
        boxShadow: "none",
      }}
    >
      <CardActionArea
        onClick={() => {
          if (
            isTimespanAvailable(
              Math.round(getEventTimestamp(events[0])),
              availableTimespans,
            )
          ) {
            setSelectedEvent(events[0]);
            setRequestedTimestamp(Math.round(getEventTimestamp(events[0])));
            return;
          }

          setSelectedEvent(events[0]);
          setRequestedTimestamp(null);
        }}
      >
        <Grid
          container
          direction="row"
          justifyContent="flex-end"
          alignItems="center"
        >
          <Grid item xs={8}>
            {getText(events)}
          </Grid>
          <Grid item xs={4}>
            <CardMedia
              sx={{
                borderRadius: "5px",
                overflow: "hidden",
              }}
            >
              <LazyLoad
                overflow
                offset={100}
                placeholder={
                  <VideoPlayerPlaceholder
                    aspectRatio={camera.width / camera.height}
                  />
                }
              >
                <Image
                  src={getSrc(events[0])}
                  color={theme.palette.background.default}
                  animationDuration={1000}
                  imageStyle={{
                    objectFit: "contain",
                  }}
                />
              </LazyLoad>
            </CardMedia>
          </Grid>
        </Grid>
      </CardActionArea>
    </Card>
  );
};
