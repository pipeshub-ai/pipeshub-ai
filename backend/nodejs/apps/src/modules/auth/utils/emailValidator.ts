export default {
    validator: function(str:string) {
      return /^\S+@\S+\.\S+$/.test(str);
    },
    message: (props:any) => `${props.value} is not a valid email!`,
  };
  
